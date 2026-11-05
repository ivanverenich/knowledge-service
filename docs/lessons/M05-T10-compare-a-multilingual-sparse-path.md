# M05-T10 — Compare a multilingual sparse path

Source task: [M05-T10](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T10-compare-a-multilingual-sparse-path.md)

## What you will do

Word search in PostgreSQL is weak for Ukrainian: the `simple` configuration does not know that "відпустка" and "відпустки" are the same word. BGE-M3, the model you run locally since M05-T09, has a second output that may fix this: for every text it gives a short list of word pieces, each with a weight. In this lesson you build a prototype search on those weights, run the 16 seed questions with it and with PostgreSQL, and decide whether it is worth keeping. Nothing in production changes: the default search stays dense plus PostgreSQL words.

- The BGE-M3 server gets a second endpoint, `POST /v1/sparse`, that returns these weights for the pinned model.
- A small port, `SparseModel`, says what such a model promises, and `LocalEmbeddingModel` fills it.
- `search_sparse_chunks` ranks Chunks by the weights. The permission check runs in PostgreSQL, in the same statement as the ranking, so a Chunk the reader may not see is never ranked.
- `compare_sparse` and the script's `--sparse` option run every case both ways and report quality, cost, and latency.
- You write a keep or reject decision, backed by those numbers.

| The task asks for | Where it happens |
|---|---|
| A BGE-M3 sparse retrieval prototype | Steps 2 to 7: server endpoint, port and adapter, search module, comparison |
| Against PostgreSQL, for Ukrainian | Steps 7 and 9: the same 16 cases, PostgreSQL words and sparse weights, sliced by language |
| Without changing the production default | No migration, no new default branch; the index lives in memory for one evaluation run |
| Deterministic tests: success, highest-risk edge, typed failure | Step 8: the seed run; a restricted Chunk with the best score is never returned; a model failure while indexing raises `CorpusNotEmbedded` |
| A report on quality, latency, operational cost, and a keep/reject decision | Step 9: the report's slices, "Cost", and "Latency" sections, and your decision in the evidence |
| No seeded sensitive text in telemetry | Steps 3, 5, and 8: answers, errors, logs, and reports are checked for a planted text |

This lesson includes coding: Steps 2 to 8 write code. Steps 1, 9, and 10 do not involve code. Step 9 runs the real model locally; it costs nothing.

Terms used in this lesson:

- **Token ID:** the number of a word piece in the model's vocabulary. BGE-M3 knows 250,002 pieces; "▁VPN" is one of them.
- **Sparse vector:** a list of (token ID, weight) pairs for the pieces a text holds. It is "sparse" because almost all of the 250,002 possible entries are zero and are left out. A dense vector, by contrast, has 1024 numbers that are all used.
- **Weight:** how much a piece matters in this text. A rare, telling piece such as "▁VPN" gets a high weight; a comma gets a low one or none.
- **Score:** how well a Chunk matches a question: multiply the weights of every piece they share and add the products up. No shared piece means no score.
- **Postings (inverted index):** for each token ID, the Chunks that hold it, like the index at the back of a book that lists the pages for each word.
- **Median latency:** the middle time when all times are sorted. Half the questions are faster, half slower; one slow outlier does not move it.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Why BGE-M3 sparse and not a separate search engine.** The task allows either. A separate engine such as OpenSearch with a Ukrainian analyser means a new service to run, secure, and keep in sync. BGE-M3 is already pinned and running from M05-T09; its sparse output needs one more file of 3.5 KB, `sparse_linear.pt`, which the server pins by its fingerprint like the others.
- **How the weights are made.** The model reads the text once. For each piece it computes a hidden state, a list of 1024 numbers, and one small layer turns that into a number. Below zero becomes zero. A piece that appears twice keeps its larger weight, and the special tokens that mark the start and end of a text are left out. This is what BGE-M3's own code does.
- **Where the index lives.** In memory, for one evaluation run. Storing it would need a migration and a new column, which changes production for an experiment that may be rejected. The report estimates what storing it would cost: pgvector's `sparsevec` type uses 8 bytes per weight.
- **How the permission check stays in the database.** Scores are computed in Python from the postings. They go to PostgreSQL as two lists, Chunk IDs and scores, which `unnest` turns into a small table. The query joins that table to `chunks` and `documents` and applies `eligible_chunks_predicate` in the same WHERE as ORDER BY and LIMIT. So a restricted Chunk can have the best score and still is never ranked, never counted, and its text never leaves PostgreSQL. A Chunk deleted after indexing has no row and drops out the same way.
- **One model per index.** The index remembers which model weighted the Chunks. A query weighted by another model raises `SparseIndexMismatch` before PostgreSQL is used, as dense search does with Embedding Spaces.
- **Nothing from the model is trusted.** Every weight must be a finite number above 0 and at most 1000, every token ID inside the vocabulary, in ascending order. The bound keeps sums from overflowing to infinity. A text cannot have more weights than tokens, so one broken answer cannot swell the index.
- **The highest-risk edge.** A restricted Chunk that matches the question best. Without the permission check it would be first. The test builds exactly this case: the query is the restricted Chunk's own text.
- **Measuring cost and latency.** The report adds a "Cost" section (indexed Chunks, stored weights, estimated storage, model calls per question) and a "Latency" section (index build time, median and slowest time per case). Latency changes from run to run, so it comes last: everything above it must be identical in two runs.
- **What the decision weighs.** Quality per slice, especially `uk->uk` and morphology. The extra model call per question; but with local dense search the question already goes to the same server, so one pass could return both outputs. And resilience: today, when the model server is down, PostgreSQL word search still answers. If sparse search replaced it, a model outage would take both branches down.
- **Known limits.** Only Chunk text is weighted, while PostgreSQL also weighs the title and headings. Literal `<s>` or `</s>` in a text are read as special tokens and match nothing. A batch pads every text to its longest one, so one long text slows the whole batch (as for dense embeddings). A sparse branch error such as `SparseIndexMismatch` propagates instead of degrading a hybrid search, because it means a wrong setup. Sixteen cases are few. Cross-language questions share few pieces by nature, so sparse search is not expected to fix them.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write down what must be true when the lesson is done and what you expect the sparse weights to do. Predictions written now, before the run, are what Step 9 checks.

Create `docs/evidence/M05-T10-multilingual-sparse-path.md` with this skeleton, then replace the prediction prompts with your own sentences:

```markdown
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

- `uk->uk` and morphology, sparse against PostgreSQL: ...
- Cross-language (`en->uk`, `uk->en`): ...
- Identifiers such as "VPN-4031": ...
- Median latency per question, sparse against PostgreSQL: ...
- My expected decision, keep or reject, and the one number that would change it: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, and every prediction is a full sentence that a number in Step 9 can prove wrong.

### Step 2 — Coding step: give the server a sparse endpoint

This step teaches the BGE-M3 server to return sparse weights next to dense vectors.

**In plain words:** The server already reads every text with the model to make a dense vector. Now it can also run the model's small sparse layer on the same reading and return, for each text, the word pieces it holds with one weight each. It pins that layer's file by fingerprint like the others, keeps every limit, and refuses to send weights that are not positive finite numbers inside the vocabulary.

| Change | What it does |
|---|---|
| `VOCABULARY_SIZE`, `sparse_linear.pt` in `FILE_HASHES` | the vocabulary bound, and the pinned sparse layer; listing it there also makes `download_model` fetch it |
| `Encoder.encode_sparse` | what the server needs from a model for sparse weights; a fake in tests |
| `encoded` | the shared part of both endpoints: limits, one batch at a time, failures without text |
| `POST /v1/sparse` | checks limits, encodes, then returns `[token ID, weight]` pairs per text, by token ID, with model, revision, and vocabulary size |
| `BgeM3Encoder.encode_sparse` | the real computation: hidden state per token, the sparse layer, `relu`, largest weight per repeated token, special tokens left out |
| `_sparse_pairs` | refuses a result with the wrong count, a token ID outside the vocabulary, or a weight that is not a positive finite float |

In `ml/bge_m3_server/bge_m3_server.py`, make these changes in order:

Find in `ml/bge_m3_server/bge_m3_server.py`:

```python
- **Unit-length dense vectors** of 1024 numbers, one per input, in order.
- **No text in errors or logs.** Error bodies hold a reason and an index;
```

Replace with:

```python
- **Unit-length dense vectors** of 1024 numbers, one per input, in order.
- **Sparse term weights** from the same pass through the model: for each
  input, the vocabulary pieces it contains, each with one positive weight
  (BGE-M3's lexical matching output). Special tokens are left out.
- **No text in errors or logs.** Error bodies hold a reason and an index;
```

Find in `ml/bge_m3_server/bge_m3_server.py`:

```python
from collections.abc import Sequence
```

Replace with:

```python
from collections.abc import Callable, Sequence
```

Find in `ml/bge_m3_server/bge_m3_server.py`:

```python
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
```

Replace with:

```python
# Token IDs in sparse weights are below this: the size of the vocabulary.
VOCABULARY_SIZE = 250_002
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
    # The layer that turns each token's hidden state into its sparse weight.
    "sparse_linear.pt": "45c93804d2142b8f6d7ec6914ae23a1eee9c6a1d27d83d908a20d2afb3595ad9",  # noqa: E501
```

Find in `ml/bge_m3_server/bge_m3_server.py`:

```python
        """Return one unit-length vector per text, in order."""
        ...
```

Replace with:

```python
        """Return one unit-length vector per text, in order."""
        ...

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        """Return, per text in order, a weight for each token ID it contains."""
        ...
```

Find in `ml/bge_m3_server/bge_m3_server.py`:

```python
    @app.post("/v1/embeddings")
    async def embeddings(body: EmbedRequest) -> JSONResponse:
        # Counting takes time on long texts; off the event loop, so a large
        # request cannot hold up every other request.
        checked = await asyncio.to_thread(_check_limits, encoder, body.inputs)
        if isinstance(checked, JSONResponse):
            return checked
        total = checked
        vectors: list[list[float]] = []
        failure: str | None = None
        async with busy:
            try:
                vectors = await asyncio.to_thread(encoder.encode, body.inputs)
            except Exception as error:
                # Its message or traceback could quote an input; keep the type.
                failure = type(error).__name__
        if failure is None and not _valid(vectors, len(body.inputs)):
            failure = "InvalidVectors"
        if failure is not None:
            logger.warning("encoding failed error=%s", failure)
            return _refusal(500, "encoding_failed")
        logger.info("embedded inputs=%d tokens=%d", len(body.inputs), total)
        return JSONResponse(
            {**_provenance(), "input_tokens": total, "vectors": vectors}
        )

    return app


class BgeM3Encoder:
    """BGE-M3 dense embeddings through sentence-transformers."""

    def __init__(self, folder: Path, device: str) -> None:
        # Imported here, so tests with a fake encoder never load PyTorch.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(str(folder), device=device)
        self._tokenizer: Any = self._model.tokenizer

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer(text, add_special_tokens=True)["input_ids"])

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            batch_size=ENCODE_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in vectors]
```

Replace with:

```python
    async def encoded(
        inputs: list[str], work: Callable[[Sequence[str]], Any]
    ) -> tuple[int, Any] | JSONResponse:
        """Check the limits, then run `work` on the inputs, one batch at a time."""
        # Counting takes time on long texts; off the event loop, so a large
        # request cannot hold up every other request.
        checked = await asyncio.to_thread(_check_limits, encoder, inputs)
        if isinstance(checked, JSONResponse):
            return checked
        failure: str | None = None
        result: Any = None
        async with busy:
            try:
                result = await asyncio.to_thread(work, inputs)
            except Exception as error:
                # Its message or traceback could quote an input; keep the type.
                failure = type(error).__name__
        if failure is not None:
            logger.warning("encoding failed error=%s", failure)
            return _refusal(500, "encoding_failed")
        return checked, result

    @app.post("/v1/embeddings")
    async def embeddings(body: EmbedRequest) -> JSONResponse:
        outcome = await encoded(body.inputs, encoder.encode)
        if isinstance(outcome, JSONResponse):
            return outcome
        total, vectors = outcome
        if not _valid(vectors, len(body.inputs)):
            logger.warning("encoding failed error=InvalidVectors")
            return _refusal(500, "encoding_failed")
        logger.info("embedded inputs=%d tokens=%d", len(body.inputs), total)
        return JSONResponse(
            {**_provenance(), "input_tokens": total, "vectors": vectors}
        )

    @app.post("/v1/sparse")
    async def sparse(body: EmbedRequest) -> JSONResponse:
        outcome = await encoded(body.inputs, encoder.encode_sparse)
        if isinstance(outcome, JSONResponse):
            return outcome
        total, weights = outcome
        pairs = _sparse_pairs(weights, len(body.inputs))
        if pairs is None:
            logger.warning("encoding failed error=InvalidWeights")
            return _refusal(500, "encoding_failed")
        logger.info("sparse inputs=%d tokens=%d", len(body.inputs), total)
        return JSONResponse(
            {
                "model": MODEL_ID,
                "revision": REVISION,
                "vocabulary_size": VOCABULARY_SIZE,
                "input_tokens": total,
                "weights": pairs,
            }
        )

    return app


class BgeM3Encoder:
    """BGE-M3 dense embeddings and sparse weights through sentence-transformers."""

    def __init__(self, folder: Path, device: str) -> None:
        # Imported here, so tests with a fake encoder never load PyTorch.
        import torch
        from sentence_transformers import SentenceTransformer

        self._torch: Any = torch
        self._model = SentenceTransformer(str(folder), device=device)
        self._tokenizer: Any = self._model.tokenizer
        # The transformer under the pooling layer: its hidden state per token.
        self._backbone: Any = self._model[0].auto_model
        # BGE-M3's sparse head: one weight per token from its hidden state.
        self._sparse: Any = torch.nn.Linear(DIMENSIONS, 1)
        self._sparse.load_state_dict(
            torch.load(
                folder / "sparse_linear.pt", map_location="cpu", weights_only=True
            )
        )
        self._sparse.to(device).eval()
        self._device = device
        self._special = {
            self._tokenizer.cls_token_id,
            self._tokenizer.eos_token_id,
            self._tokenizer.pad_token_id,
            self._tokenizer.unk_token_id,
        }

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer(text, add_special_tokens=True)["input_ids"])

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            batch_size=ENCODE_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in vectors]

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        weights: list[dict[int, float]] = []
        for start in range(0, len(texts), ENCODE_BATCH_SIZE):
            weights += self._sparse_batch(
                list(texts[start : start + ENCODE_BATCH_SIZE])
            )
        return weights

    def _sparse_batch(self, texts: list[str]) -> list[dict[int, float]]:
        # As BGE-M3's own code does: relu(linear(hidden state)) per token, the
        # largest weight when a token repeats, special tokens left out.
        features = self._tokenizer(texts, padding=True, return_tensors="pt")
        features = {name: value.to(self._device) for name, value in features.items()}
        with self._torch.inference_mode():
            hidden = self._backbone(**features).last_hidden_state
            token_weights = self._torch.relu(self._sparse(hidden)).squeeze(-1)
        result: list[dict[int, float]] = []
        for ids, values, mask in zip(
            features["input_ids"].tolist(),
            token_weights.tolist(),
            features["attention_mask"].tolist(),
            strict=True,
        ):
            weights: dict[int, float] = {}
            for token, value, kept in zip(ids, values, mask, strict=True):
                if kept and token not in self._special and value > 0:
                    weights[token] = max(value, weights.get(token, 0.0))
            result.append(weights)
        return result
```

Find in `ml/bge_m3_server/bge_m3_server.py`:

```python

def _count(encoder: Encoder, text: str) -> int | None:
```

Replace with:

```python

def _sparse_pairs(weights: object, expected: int) -> list[list[list[float]]] | None:
    """Return each text's weights as [token ID, weight] pairs by token ID, or None.

    None when the count, a token ID, or a weight is wrong: a weight must be a
    finite number above zero, and a token ID an int inside the vocabulary.
    """
    if not isinstance(weights, list) or len(weights) != expected:
        return None
    result: list[list[list[float]]] = []
    for item in weights:
        if not isinstance(item, dict):
            return None
        pairs: list[list[float]] = []
        for token, value in sorted(item.items()):
            if (
                type(token) is not int
                or not 0 <= token < VOCABULARY_SIZE
                or type(value) is not float
                or not math.isfinite(value)
                or value <= 0
            ):
                return None
            pairs.append([token, value])
        result.append(pairs)
    return result


def _count(encoder: Encoder, text: str) -> int | None:
```

Check: start the server from the repository root (it downloads the 3.5 KB sparse layer on the first start):

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

In a second terminal:

```bash
curl -s 127.0.0.1:8765/v1/sparse -H 'Content-Type: application/json' -d '{"inputs": ["VPN error 4031"]}'
```

Expected: a JSON object with `"vocabulary_size":250002`, `"input_tokens":6`, and one list of four `[token ID, weight]` pairs, all weights between 0 and 1. If port 8765 is taken, start the server with `BGE_M3_PORT=8766` and use that port. Stop the server with Ctrl+C.

### Step 3 — Coding step: test the sparse endpoint

This step tests the new endpoint without loading the model, plus one test with the real model.

**In plain words:** The fake model gets a sparse method that returns fixed weights, or broken ones on request. The tests check that good weights come back in token order with the model named, that the same limits apply, and that a negative, zero, NaN, infinite, or out-of-vocabulary weight is refused without repeating the text. One test loads the real model and checks that its weights are positive, skip special tokens, and are the same when asked twice.

| Test | Proves |
|---|---|
| `test_sparse_weights_name_the_pinned_model_and_come_by_token_id` | success: pairs by token ID, with model, revision, vocabulary size, token count |
| `test_sparse_weights_obey_the_same_limits` | a too-long text is refused and never encoded |
| `test_invalid_sparse_weights_are_refused_without_text` | nine kinds of bad weights give `encoding_failed`, with no text in the answer or logs |
| `test_a_sparse_encoding_failure_carries_no_text` | a model that raises leaks nothing |
| `test_real_sparse_weights_are_positive_repeatable_and_skip_special_tokens` | the real model: "▁VPN" is weighted, weights are positive, special tokens absent, repeated calls agree |

In `ml/bge_m3_server/test_bge_m3_server.py`:

Find in `ml/bge_m3_server/test_bge_m3_server.py`:

```python

import pytest
from fastapi.testclient import TestClient

import bge_m3_server as server

PLANTED = "the salary of Olena is 9000"


class FakeEncoder:
    """Counts one token per character and records what it encoded."""

    def __init__(self, *, fail: bool = False, value: float = 1.0) -> None:
        self.encoded: list[list[str]] = []
        self._fail = fail
        self._value = value

    def count_tokens(self, text: str) -> int:
        if text == "uncountable":
            raise ValueError(f"cannot tokenize {text}")
        return len(text)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        return [[self._value] + [0.0] * (server.DIMENSIONS - 1) for _ in texts]
```

Replace with:

```python
from typing import cast

import pytest
from fastapi.testclient import TestClient

import bge_m3_server as server

PLANTED = "the salary of Olena is 9000"


class FakeEncoder:
    """Counts one token per character and records what it encoded."""

    def __init__(
        self,
        *,
        fail: bool = False,
        value: float = 1.0,
        sparse: object = None,
    ) -> None:
        self.encoded: list[list[str]] = []
        self._fail = fail
        self._value = value
        # What `encode_sparse` returns for every text; one pair by default.
        self._sparse = {7: 0.5, 3: 0.25} if sparse is None else sparse

    def count_tokens(self, text: str) -> int:
        if text == "uncountable":
            raise ValueError(f"cannot tokenize {text}")
        return len(text)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        return [[self._value] + [0.0] * (server.DIMENSIONS - 1) for _ in texts]

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        # Deliberately unchecked: tests pass weights the server must refuse.
        return [cast(dict[int, float], self._sparse) for _ in texts]
```

Find in `ml/bge_m3_server/test_bge_m3_server.py`:

```python

def _cached_model() -> Path | None:
```

Replace with:

```python

def test_sparse_weights_name_the_pinned_model_and_come_by_token_id() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/sparse", json={"inputs": ["a", "bc"]})

    assert answer.status_code == 200
    assert answer.json() == {
        "model": "BAAI/bge-m3",
        "revision": server.REVISION,
        "vocabulary_size": server.VOCABULARY_SIZE,
        "input_tokens": 3,
        "weights": [[[3, 0.25], [7, 0.5]], [[3, 0.25], [7, 0.5]]],
    }
    assert encoder.encoded == [["a", "bc"]]


def test_sparse_weights_obey_the_same_limits() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post(
        "/v1/sparse", json={"inputs": ["x" * (server.MAX_INPUT_TOKENS + 1)]}
    )

    assert answer.status_code == 413
    assert answer.json() == {"error": "input_too_long", "index": 0}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "weights",
    [
        {7: -0.5},
        {7: 0.0},
        {7: float("nan")},
        {7: float("inf")},
        {server.VOCABULARY_SIZE: 0.5},
        {-1: 0.5},
        {"7": 0.5},
        {7: 1},
        [0.5],
    ],
    ids=[
        "negative",
        "zero",
        "not a number",
        "infinite",
        "outside the vocabulary",
        "negative token",
        "token as text",
        "weight as int",
        "not a mapping",
    ],
)
def test_invalid_sparse_weights_are_refused_without_text(
    weights: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeEncoder(sparse=weights)).post(
        "/v1/sparse", json={"inputs": [PLANTED]}
    )

    assert answer.status_code == 500
    assert answer.json() == {"error": "encoding_failed", "index": None}
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


def test_a_sparse_encoding_failure_carries_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeEncoder(fail=True)).post(
        "/v1/sparse", json={"inputs": [PLANTED]}
    )

    assert answer.status_code == 500
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


def _cached_model() -> Path | None:
```

At the end of `ml/bge_m3_server/test_bge_m3_server.py`, add:

```python


@pytest.mark.model
def test_real_sparse_weights_are_positive_repeatable_and_skip_special_tokens() -> None:
    folder = _cached_model()
    if folder is None or not (folder / "sparse_linear.pt").is_file():
        pytest.skip("the pinned BGE-M3 files are not in the Hugging Face cache")
    from tokenizers import Tokenizer

    encoder = server.BgeM3Encoder(folder, "cpu")
    texts = ["Помилка VPN-4031 під час підключення", "відпустка"]

    first = encoder.encode_sparse(texts)
    second = encoder.encode_sparse(list(reversed(texts)))

    assert first == list(reversed(second))
    tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
    vpn = tokenizer.token_to_id("\N{LOWER ONE EIGHTH BLOCK}VPN")
    assert vpn in first[0]
    special = {0, 1, 2, 3}  # <s>, <pad>, </s>, <unk>
    for weights in first:
        assert weights
        assert not special & weights.keys()
        assert all(0 < value < 10 for value in weights.values())
```

Check: `make ml-check` reports `0 errors` and `29 passed`. The real-model test needs `sparse_linear.pt` in the cache, which the server start in Step 2 downloaded; without it, the test skips.

### Step 4 — Coding step: the sparse port and the adapter

This step defines what any sparse model promises, and makes `LocalEmbeddingModel` keep that promise.

**In plain words:** A new small module says what sparse weights look like and checks a model's answer without trusting it. The local adapter gets a second method that sends texts to the new endpoint, checks the model and version named in the answer, refuses a text with more weights than tokens, and turns every problem into the same typed errors as dense embedding. While here, the adapter also refuses compressed answers: a small compressed body could unpack to far more than the 8 MB cap.

| Piece | What it does |
|---|---|
| `SparseVector` | `(token ID, weight)` pairs in ascending token order |
| `MAX_SPARSE_WEIGHT` | 1000: far above BGE-M3's real weights, low enough that no sum overflows |
| `SparseModelIdentity` | provider, model, vocabulary size; weights of two identities never mix |
| `SparseModel` | `sparse_identity`, `limits`, `count_tokens`, `encode_sparse` |
| `checked_sparse_vectors` | refuses a wrong count, a bad pair, token, or weight, or pairs out of order, with `EmbeddingMalformedResponse` |
| `LocalEmbeddingModel.encode_sparse` | checks input, sends one request to `/v1/sparse`, checks provenance and every weight, logs counts only |
| `_send` | asks for an uncompressed answer and refuses one that is compressed, before reading it |

Create `src/knowledge_service/embeddings/sparse.py`:

```python
"""Sparse term weights: which vocabulary pieces a text holds, and how much each counts.

A sparse model such as BGE-M3 gives every text a short list of token IDs
from its vocabulary, each with a positive weight. Two texts match as much as
the weights of the tokens they share: the sum of the products, so a rare,
important piece shared by both counts far more than a shared comma. Unlike
a dense vector, almost every entry is zero, so only the others are kept.

This module is the port a sparse model fills. It is a prototype for the
M05-T10 comparison with PostgreSQL full-text search; nothing in production
uses it.

What a `SparseVector` promises, once `checked_sparse_vectors` returned it:

- **Pairs by token ID.** `(token_id, weight)` pairs in ascending token ID
  order, each ID once, each an int inside the model's vocabulary.
- **Positive, bounded weights.** Above zero, since a zero weight is left
  out instead, and at most `MAX_SPARSE_WEIGHT`, so multiplying and summing
  them can never overflow to infinity and flatten the ranking.
- **Possibly empty.** A text of only punctuation may hold no weighted token;
  it then matches nothing.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.embeddings.port import (
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    InvalidEmbedding,
)

type SparseVector = tuple[tuple[int, float], ...]

# BGE-M3's weights stay well below 10. A bound far above that keeps every
# product and every sum of up to 8192 of them a finite number.
MAX_SPARSE_WEIGHT = 1_000.0


@dataclass(frozen=True, slots=True)
class SparseModelIdentity:
    """Which model produced sparse weights; weights of two identities never mix."""

    provider: str
    model: str
    vocabulary_size: int

    def __post_init__(self) -> None:
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidEmbedding("provider and model must be strings")
        if not self.provider.strip() or not self.model.strip():
            raise InvalidEmbedding("provider and model must not be empty")
        if type(self.vocabulary_size) is not int or self.vocabulary_size < 1:
            raise InvalidEmbedding("vocabulary_size must be a positive integer")


class SparseModel(Protocol):
    """A model that turns batches of texts into sparse term weights."""

    @property
    def sparse_identity(self) -> SparseModelIdentity:
        """The provider, model, and vocabulary size."""
        ...

    @property
    def limits(self) -> EmbeddingLimits:
        """The most one call to `encode_sparse` may carry."""
        ...

    def count_tokens(self, text: str) -> int:
        """Count `text` in this model's tokens, never fewer than the model does."""
        ...

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        """Return one sparse vector per text, in order, or raise an `EmbeddingError`."""
        ...


def checked_sparse_vectors(
    items: object, *, expected: int, vocabulary_size: int
) -> tuple[SparseVector, ...]:
    """Return the model's weights as sparse vectors, or raise if any is invalid.

    `items` is whatever the model sent: a list with one list of
    `[token_id, weight]` pairs per text. Nothing about its shape is trusted;
    any fault raises `EmbeddingMalformedResponse` naming the text's index.
    """
    vectors = _list(items)
    if vectors is None or len(vectors) != expected:
        raise EmbeddingMalformedResponse(f"expected {expected} sparse vectors")
    return tuple(
        _checked(item, index, vocabulary_size) for index, item in enumerate(vectors)
    )


def _checked(item: object, index: int, vocabulary_size: int) -> SparseVector:
    pairs = _list(item)
    if pairs is None:
        raise EmbeddingMalformedResponse(f"sparse vector {index} is not a list")
    result: list[tuple[int, float]] = []
    for pair in pairs:
        values = _list(pair)
        if values is None or len(values) != 2:
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad pair")
        token, weight = values
        # bool is an int, so `True` would otherwise pass as token 1.
        if type(token) is not int or not 0 <= token < vocabulary_size:
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad token")
        if (
            type(weight) is not float
            or not math.isfinite(weight)
            or not 0 < weight <= MAX_SPARSE_WEIGHT
        ):
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad weight")
        if result and token <= result[-1][0]:
            raise EmbeddingMalformedResponse(
                f"sparse vector {index} is not ordered by token"
            )
        result.append((token, weight))
    return tuple(result)


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None
```

Then, in `src/knowledge_service/embeddings/local_adapter.py`:

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory.
```

Replace with:

```python
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory. A
  compressed answer is refused unread, because it could unpack to far more.
- **Sparse weights, as a prototype.** `encode_sparse` asks the same server
  for BGE-M3's sparse term weights (see `embeddings.sparse`), with the same
  limits, provenance check, size cap, and errors. Only the M05-T10
  comparison uses them.
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
```

Replace with:

```python
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
SPARSE_PATH = "/v1/sparse"
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
    dimensions: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
```

Replace with:

```python
    dimensions: int = Field(default=1024, gt=0)
    vocabulary_size: int = Field(default=250_002, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
        self._limits = EmbeddingLimits(
            max_inputs=settings.max_inputs,
            max_input_tokens=settings.max_input_tokens,
            max_batch_tokens=settings.max_batch_tokens,
        )

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity
```

Replace with:

```python
        self._sparse_identity = SparseModelIdentity(
            provider=PROVIDER,
            model=f"{settings.model}@{settings.revision}",
            vocabulary_size=settings.vocabulary_size,
        )
        self._limits = EmbeddingLimits(
            max_inputs=settings.max_inputs,
            max_input_tokens=settings.max_input_tokens,
            max_batch_tokens=settings.max_batch_tokens,
        )

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def sparse_identity(self) -> SparseModelIdentity:
        return self._sparse_identity
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python

    async def verify_server(self, *, timeout_seconds: float) -> None:
```

Replace with:

```python

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        """Return BGE-M3's sparse term weights for each text, in order.

        Checked and refused exactly like `embed`; the answer must also name
        the configured model, revision, and vocabulary size.
        """
        deadline = self._deadline(timeout_seconds)
        try:
            check_inputs(texts, self._limits, self.count_tokens)
        except EmbeddingInputRejected as error:
            _log(request_id, self._identity, len(texts), 0, error, operation="sparse")
            raise
        if not texts:
            return ()
        started_at = time.perf_counter()
        try:
            status, content = await self._send(
                "POST",
                SPARSE_PATH,
                deadline,
                body={"inputs": list(texts)},
                headers={"X-Request-ID": str(request_id)},
            )
            vectors = self._sparse_from(status, content, texts)
        except EmbeddingError as error:
            _log(
                request_id,
                self._identity,
                len(texts),
                _since(started_at),
                error,
                operation="sparse",
            )
            raise
        _log(
            request_id,
            self._identity,
            len(texts),
            _since(started_at),
            None,
            operation="sparse",
        )
        return vectors

    async def verify_server(self, *, timeout_seconds: float) -> None:
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
        """Send one request; return its status and its bytes, up to the cap."""
        status = 0
        content = bytearray()
        failure: EmbeddingError | None = None
        try:
            async with (
                asyncio.timeout(deadline),
                self._client.stream(
                    method,
                    self._settings.url + path,
                    json=body,
                    headers=headers,
                    timeout=deadline,
                ) as response,
            ):
                status = response.status_code
                async for piece in response.aiter_bytes():
                    content += piece
                    if len(content) > MAX_RESPONSE_BYTES:
                        break
        except TimeoutError, httpx.TimeoutException:
            failure = EmbeddingTimeout()
        except httpx.HTTPError:
            failure = EmbeddingUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = EmbeddingMalformedResponse("the response is too large")
```

Replace with:

```python
        """Send one request; return its status and its bytes, up to the cap.

        The answer must not be compressed: a small compressed body can unpack
        to far more than the cap before the cap is checked.
        """
        status = 0
        content = bytearray()
        encoded = False
        failure: EmbeddingError | None = None
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
            failure = EmbeddingTimeout()
        except httpx.HTTPError:
            failure = EmbeddingUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = EmbeddingMalformedResponse("the response is too large")
        if encoded and failure is None:
            failure = EmbeddingMalformedResponse("the response is encoded")
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
            expected=expected,
        )

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
```

Replace with:

```python
            expected=expected,
        )

    def _sparse_from(
        self, status: int, content: bytes, texts: Sequence[str]
    ) -> tuple[SparseVector, ...]:
        _check_status(status)
        body = _json_object(content)
        if body is None:
            raise EmbeddingMalformedResponse("the local model response was not JSON")
        size = body.get("vocabulary_size")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
            or type(size) is not int
            or size != self._settings.vocabulary_size
        ):
            # Weights from another vocabulary would match the wrong tokens.
            raise EmbeddingRequestRefused(
                "the local model is not the configured model, revision, or vocabulary"
            )
        vectors = checked_sparse_vectors(
            body.get("weights"),
            expected=len(texts),
            vocabulary_size=self._settings.vocabulary_size,
        )
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
            # A text has at most one weight per token; more would swell the index.
            if len(vector) > self.count_tokens(text):
                raise EmbeddingMalformedResponse(
                    f"sparse vector {index} has more weights than its text has tokens"
                )
        return vectors

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
    usage: EmbeddingUsage | None = None,
) -> None:
```

Replace with:

```python
    usage: EmbeddingUsage | None = None,
    *,
    operation: str | None = None,
) -> None:
```

Find in `src/knowledge_service/embeddings/local_adapter.py`:

```python
        fields["input_tokens"] = usage.input_tokens
    level = logging.INFO if error is None else logging.WARNING
```

Replace with:

```python
        fields["input_tokens"] = usage.input_tokens
    if operation is not None:
        fields["operation"] = operation
    level = logging.INFO if error is None else logging.WARNING
```

Check: `make typecheck` reports `0 errors`.

### Step 5 — Coding step: test the adapter's sparse method

This step tests `encode_sparse` against the in-process stand-in for the server.

**In plain words:** The test helpers learn to answer `/v1/sparse`. A fake sparse model turns each word into one token, so texts that share words share tokens, as real ones do. The tests check a good answer, then every way an answer can be wrong: another model or vocabulary, a bad weight, too many weights for the text, a compressed body, and a server that is down, with no text in errors or logs.

| Piece | What it does |
|---|---|
| `word_weights`, `FakeSparseModel` (`tests/fakes.py`) | deterministic fake weights; a `SparseModel` that records every batch |
| `StubBgeM3Server` (`tests/local_stubs.py`) | answers `/v1/sparse`; can send other provenance, bad weights, or extra headers |
| `test_sparse_weights_come_from_the_pinned_model_in_order` | success |
| `test_sparse_weights_from_another_model_are_refused` | another revision, model, or vocabulary raises `EmbeddingRequestRefused` |
| `test_malformed_sparse_weights_are_refused` | nine bad shapes raise `EmbeddingMalformedResponse` |
| `test_a_sparse_failure_carries_no_text_in_its_chain_or_the_logs` | a stopped server is `EmbeddingUnavailable`, quietly |
| `test_more_sparse_weights_than_the_text_has_tokens_are_refused` | one answer cannot swell the index |
| `test_a_compressed_answer_is_refused_before_it_is_unpacked` | the 8 MB cap holds for compressed bodies |

In `tests/fakes.py`:

Find in `tests/fakes.py`:

```python
import math
from collections.abc import Sequence
```

Replace with:

```python
import math
import re
from collections.abc import Sequence
```

Find in `tests/fakes.py`:

```python
    checked_batch,
)
```

Replace with:

```python
    checked_batch,
)
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)
```

At the end of `tests/fakes.py`, add:

```python


# The BGE-M3 vocabulary size; fake token IDs stay inside it.
FAKE_VOCABULARY = 250_002


def word_weights(text: str) -> list[list[float]]:
    """Fake sparse weights: one token per lowercased word, weighted by length.

    A word's token ID is a hash of the word, so the same word always gets
    the same ID, and texts sharing words share tokens, as real ones do.
    Pairs come in token order, each token once, as the port requires.
    """
    weights: dict[int, float] = {}
    for word in re.findall(r"\w+", text.lower()):
        token = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4]) % (
            FAKE_VOCABULARY
        )
        weights[token] = max(weights.get(token, 0.0), len(word) / 10)
    return [[token, weights[token]] for token in sorted(weights)]


class FakeSparseModel:
    """Deterministic SparseModel over `word_weights`; records every batch."""

    def __init__(
        self,
        *,
        limits: EmbeddingLimits | None = None,
        error: EmbeddingError | None = None,
    ) -> None:
        self._identity = SparseModelIdentity(
            provider="fake", model="fake-sparse", vocabulary_size=FAKE_VOCABULARY
        )
        self._limits = limits or EmbeddingLimits(
            max_inputs=16, max_input_tokens=1_000, max_batch_tokens=4_000
        )
        self._error = error
        self.batches: list[tuple[str, ...]] = []

    @property
    def sparse_identity(self) -> SparseModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        check_inputs(texts, self._limits, self.count_tokens)
        self.batches.append(tuple(texts))
        if self._error is not None:
            raise self._error
        return checked_sparse_vectors(
            [word_weights(text) for text in texts],
            expected=len(texts),
            vocabulary_size=FAKE_VOCABULARY,
        )
```

In `tests/local_stubs.py`:

Find in `tests/local_stubs.py`:

```python

from knowledge_service.embeddings.local_adapter import (
```

Replace with:

```python

from fakes import word_weights
from knowledge_service.embeddings.local_adapter import (
```

Find in `tests/local_stubs.py`:

```python
    ) -> None:
        self._status = status
        self._body = body
        self._provenance = {
            "model": model,
            "revision": revision,
            "dimensions": dimensions,
        }
        self._vector_dimensions = vector_dimensions or int(dimensions)
        self._error = error
        self._block = block
```

Replace with:

```python
        vocabulary_size: object = 250_002,
        sparse_weights: object = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._provenance = {
            "model": model,
            "revision": revision,
            "dimensions": dimensions,
        }
        self._vector_dimensions = vector_dimensions or int(dimensions)
        self._error = error
        self._block = block
        self._vocabulary_size = vocabulary_size
        # Sent as the weights of every text instead of `word_weights`.
        self._sparse_weights = sparse_weights
        self._headers = headers or {}
```

Find in `tests/local_stubs.py`:

```python
            return httpx.Response(self._status, content=self._body or b"{}")
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        texts: list[str] = json.loads(request.content)["inputs"]
```

Replace with:

```python
            return httpx.Response(
                self._status, content=self._body or b"{}", headers=self._headers
            )
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        texts: list[str] = json.loads(request.content)["inputs"]
        if request.url.path == "/v1/sparse":
            return httpx.Response(
                200,
                json={
                    "model": self._provenance["model"],
                    "revision": self._provenance["revision"],
                    "vocabulary_size": self._vocabulary_size,
                    "input_tokens": 3 * len(texts),
                    "weights": [
                        word_weights(t)
                        if self._sparse_weights is None
                        else self._sparse_weights
                        for t in texts
                    ],
                },
            )
```

In `tests/test_local_embeddings.py`:

Find in `tests/test_local_embeddings.py`:

```python
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

```

Replace with:

```python
import gzip
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from fakes import word_weights
```

Find in `tests/test_local_embeddings.py`:

```python
)
from local_stubs import URL, StubBgeM3Server, local_embedding_model
```

Replace with:

```python
)
from knowledge_service.embeddings.sparse import SparseModelIdentity
from local_stubs import URL, StubBgeM3Server, local_embedding_model
```

At the end of `tests/test_local_embeddings.py`, add:

```python


async def test_sparse_weights_come_from_the_pinned_model_in_order() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server)

    vectors = await model.encode_sparse(
        ["VPN error", "відпустка"], request_id=REQUEST_ID, timeout_seconds=2.0
    )

    assert model.sparse_identity == SparseModelIdentity(
        provider="local",
        model=f"BAAI/bge-m3@{BGE_M3_REVISION}",
        vocabulary_size=250_002,
    )
    assert [list(map(list, vector)) for vector in vectors] == [
        word_weights("VPN error"),
        word_weights("відпустка"),
    ]
    assert server.requests[0].url.path == "/v1/sparse"


@pytest.mark.parametrize(
    "server",
    [
        StubBgeM3Server(revision="0" * 40),
        StubBgeM3Server(model="BAAI/bge-m3-unsupervised"),
        StubBgeM3Server(vocabulary_size=250_001),
        StubBgeM3Server(vocabulary_size=250_002.0),
    ],
    ids=["other revision", "other model", "other vocabulary", "vocabulary as float"],
)
async def test_sparse_weights_from_another_model_are_refused(
    server: StubBgeM3Server,
) -> None:
    model = local_embedding_model(server)

    with pytest.raises(EmbeddingRequestRefused):
        await model.encode_sparse(
            ["vacation"], request_id=REQUEST_ID, timeout_seconds=2.0
        )


@pytest.mark.parametrize(
    "weights",
    [
        [[7, -0.5]],
        [[7, 0.0]],
        [[7, 1]],
        [[250_002, 0.5]],
        [[True, 0.5]],
        [[9, 0.5], [3, 0.5]],
        [[3, 0.5], [3, 0.5]],
        [[3, 0.5, 1.0]],
        "weights",
    ],
    ids=[
        "negative",
        "zero",
        "int weight",
        "outside the vocabulary",
        "bool token",
        "out of order",
        "repeated token",
        "three values",
        "not a list",
    ],
)
async def test_malformed_sparse_weights_are_refused(weights: object) -> None:
    model = local_embedding_model(StubBgeM3Server(sparse_weights=weights))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.encode_sparse(
            ["vacation"], request_id=REQUEST_ID, timeout_seconds=2.0
        )


async def test_a_sparse_failure_carries_no_text_in_its_chain_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = local_embedding_model(
        StubBgeM3Server(error=httpx.ConnectError(f"refused {PLANTED}"))
    )

    with pytest.raises(EmbeddingUnavailable) as raised:
        await model.encode_sparse([PLANTED], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert all(PLANTED not in repr(error) for error in chain(raised.value))
    assert PLANTED not in caplog.text
    (record,) = caplog.records
    assert record.__dict__["embedding"]["operation"] == "sparse"


async def test_more_sparse_weights_than_the_text_has_tokens_are_refused() -> None:
    # "hi" holds at most a few tokens; thousands of pairs would swell the index.
    weights = [[token, 0.5] for token in range(5_000)]
    model = local_embedding_model(StubBgeM3Server(sparse_weights=weights))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.encode_sparse(["hi"], request_id=REQUEST_ID, timeout_seconds=2.0)


async def test_a_compressed_answer_is_refused_before_it_is_unpacked() -> None:
    # 64 MB of zeros packs into about 64 KB; unpacked, it would pass the cap.
    packed = gzip.compress(b"0" * (64 * 1024 * 1024))
    server = StubBgeM3Server(body=packed, headers={"Content-Encoding": "gzip"})

    with pytest.raises(EmbeddingMalformedResponse, match="encoded"):
        await embed(server, ["vacation policy"])
```

Check: `uv run pytest tests/test_local_embeddings.py -q` reports `50 passed`, and `uv run pytest tests/test_embedding_model_contract.py -q` still reports `18 passed, 1 deselected`.

### Step 6 — Coding step: write the sparse search

This step writes the search itself, in one new module, and gives it a branch name.

**In plain words:** To build the index, the module reads every available Chunk's text, asks the model for its weights in batches the model accepts, checks every weight, and files them by token. To search, it checks the limit, the model, and the question's weights; adds up the products for every Chunk that shares a piece with the question; then hands the scores to PostgreSQL, which drops every Chunk the reader may not see and returns the best of the rest with their text and source.

| Piece | What it does |
|---|---|
| `RetrievalBranch.SPARSE` | the branch name the comparison and reports use; no default search includes it |
| `SparseIndex` | the model identity, postings by token, and how many Chunks and weights it holds |
| `sparse_index` | checks every vector and builds the postings, in Chunk ID order, so scores are summed in the same order every time |
| `build_sparse_index` | reads every available Chunk, restricted ones included, and weights them in batches within the model's limits |
| `search_sparse_chunks` | checks limit, model, and query, before PostgreSQL; then scores, and lets PostgreSQL filter and order |
| `sparse_search_statement` | the scores as an `unnest` table, joined to Chunks, with the permission check in the same WHERE |
| `sparse_branch` | weights the question, then searches; plugs into `retrieve_hybrid` like the other branches |
| `InvalidSparseQuery`, `InvalidSparseWeights`, `SparseIndexMismatch` | typed refusals that name no text |

In `src/knowledge_service/retrieval/fusion.py`:

Find in `src/knowledge_service/retrieval/fusion.py`:

```python
    LEXICAL = "lexical"

```

Replace with:

```python
    LEXICAL = "lexical"
    # The M05-T10 prototype: BGE-M3 sparse term weights. No default uses it.
    SPARSE = "sparse"

```

Create `src/knowledge_service/retrieval/sparse_search.py`:

```python
"""Prototype: find Chunks by BGE-M3 sparse term weights, for one reader.

This is the M05-T10 experiment, not a production branch: no default uses
it, and its index lives in memory for one evaluation run. It answers one
question: does BGE-M3's sparse output find Ukrainian (and English) Chunks
better than PostgreSQL full-text search, and at what cost?

How it works:

- `build_sparse_index` reads every available Chunk, as dense embedding
  does, asks the sparse model for its weights in batches within the
  model's limits, and keeps them as postings: for each token ID, the
  Chunks that hold it and their weights.
- `search_sparse_chunks` scores a query against the postings, a sum of
  weight products over shared tokens, then lets PostgreSQL drop every
  Chunk the reader may not see and order the rest, in one statement.

What a caller can rely on in a `SparseSearchResult`:

- **Eligible.** The scores go to PostgreSQL as parameters, and the
  permission check sits in the same WHERE as the ordering, as in vector
  search: a Chunk the reader may not see is never ranked, never counted,
  and its text never leaves the database. A Chunk deleted after the index
  was built has no row, so it is never returned either.
- **Ranked.** Highest score first, then by Chunk ID; `rank` counts from 1.
  A Chunk that shares no token with the query has no score and is not a
  Candidate, so a query of only unweighted tokens returns nothing.
- **One model.** The query's weights must come from the model that built
  the index; otherwise `SparseIndexMismatch` is raised before the database
  is used.
- **Bounded.** At most `limit` Candidates, an int from 1 to
  `MAX_CANDIDATES`, and `truncated` says whether more eligible Chunks match.
- **Quiet.** Errors name no text, and the fields holding Document text are
  kept out of repr, as in every Candidate.
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion, DocumentAvailability
from knowledge_service.embeddings.sparse import (
    MAX_SPARSE_WEIGHT,
    SparseModel,
    SparseModelIdentity,
    SparseVector,
)
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult, BranchSearch
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

# Scores travel as 64-bit floats, so PostgreSQL orders exactly what was summed.
_SCORE = sa.Double[float]()


class SparseSearchError(Exception):
    """Base error for a sparse search."""


class InvalidSparseQuery(SparseSearchError):
    """The query weights or the limit cannot be searched; nothing was read."""


class InvalidSparseWeights(SparseSearchError):
    """A Chunk's weights break the sparse port's rules; nothing was indexed."""


class SparseIndexMismatch(SparseSearchError):
    """The query's weights come from another model than the index's."""


@dataclass(frozen=True, slots=True)
class SparseIndex:
    """Sparse weights of every indexed Chunk, kept by token for scoring.

    `postings` maps a token ID to the Chunks that hold it, by Chunk ID, with
    their weights. `chunks` counts the indexed Chunks and `weights` the
    stored (token, weight) pairs, which is what storing them would cost.
    """

    identity: SparseModelIdentity
    postings: Mapping[int, tuple[tuple[ChunkId, float], ...]] = field(repr=False)
    chunks: int
    weights: int


@dataclass(frozen=True, slots=True)
class SparseCandidate:
    """One eligible Chunk that shares weighted tokens with the query.

    `score` is the sum of weight products over shared tokens; higher is
    better and only the order means anything. The fields holding Document
    text are kept out of repr.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SparseSearchResult:
    """The Candidates of one sparse search; see the module docstring."""

    limit: int
    candidates: tuple[SparseCandidate, ...]
    truncated: bool


def sparse_index(
    identity: SparseModelIdentity, vectors: Mapping[ChunkId, SparseVector]
) -> SparseIndex:
    """Turn each Chunk's weights into postings by token, in Chunk ID order.

    Every vector is checked like a query, because a model is outside code:
    a NaN, negative, or huge weight raises `InvalidSparseWeights`.
    """
    postings: dict[int, list[tuple[ChunkId, float]]] = defaultdict(list)
    for chunk_id in sorted(vectors, key=lambda chunk: chunk.value):
        checked = _checked_weights(
            vectors[chunk_id], identity.vocabulary_size, InvalidSparseWeights
        )
        for token, weight in checked:
            postings[token].append((chunk_id, weight))
    return SparseIndex(
        identity=identity,
        postings={token: tuple(pairs) for token, pairs in postings.items()},
        chunks=len(vectors),
        weights=sum(len(vector) for vector in vectors.values()),
    )


async def build_sparse_index(
    runtime: DatabaseRuntime,
    model: SparseModel,
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> SparseIndex:
    """Ask `model` for the weights of every available Chunk and index them.

    Restricted Chunks are indexed too, because search must be able to score
    them for the permission check to matter. Raises the model's
    `EmbeddingError` when a batch fails; nothing is kept then.
    """
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(chunks.c.chunk_id, chunks.c.text)
                .select_from(chunks.join(documents))
                .where(documents.c.availability == DocumentAvailability.AVAILABLE.value)
                .order_by(chunks.c.chunk_id)
            )
        ).all()
    ids = [ChunkId(cast(UUID, row.chunk_id)) for row in rows]
    texts = [cast(str, row.text) for row in rows]
    identity = model.sparse_identity
    vectors: dict[ChunkId, SparseVector] = {}
    for start, end in _batches(texts, model):
        weights = await model.encode_sparse(
            texts[start:end],
            request_id=uuid5(request_id, f"sparse {start}"),
            timeout_seconds=timeout_seconds,
        )
        vectors.update(zip(ids[start:end], weights, strict=True))
    return sparse_index(identity, vectors)


async def search_sparse_chunks(
    connection: AsyncConnection,
    *,
    index: SparseIndex,
    query: SparseVector,
    identity: SparseModelIdentity,
    context: AuthorizationContext,
    limit: int,
) -> SparseSearchResult:
    """Return up to `limit` eligible Chunks, highest sparse score first.

    `identity` names the model that weighted the query. Raises
    `InvalidSparseQuery` for a bad limit or query and `SparseIndexMismatch`
    when `identity` is not the index's, both before the database is used.
    """
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidSparseQuery(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    if identity != index.identity:
        raise SparseIndexMismatch("the query was weighted by another model")
    checked = _checked_weights(query, identity.vocabulary_size, InvalidSparseQuery)
    scores = _scores(index, checked)
    if not scores:
        return SparseSearchResult(limit=limit, candidates=(), truncated=False)
    statement = sparse_search_statement(
        scores=scores,
        eligibility=eligible_chunks_predicate(context),
        # One row more than asked shows whether more eligible Chunks match.
        limit=limit + 1,
    )
    records = (await connection.execute(statement)).mappings().all()
    return SparseSearchResult(
        limit=limit,
        candidates=tuple(
            _candidate_from(dict(record), rank=position)
            for position, record in enumerate(records[:limit], start=1)
        ),
        truncated=len(records) > limit,
    )


def sparse_search_statement(
    *,
    scores: Mapping[ChunkId, float],
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the best-scored Chunks that meet `eligibility`.

    The scores arrive as two array parameters and become a table with
    `unnest`; the permission check sits in the same WHERE, so PostgreSQL
    drops ineligible Chunks before ORDER BY and LIMIT. Ties in score are
    broken by Chunk ID.
    """
    ids = sorted(scores, key=lambda chunk: chunk.value)
    scored = (
        sa.func.unnest(
            sa.bindparam("chunk_ids", [c.value for c in ids], type_=ARRAY(sa.Uuid())),
            sa.bindparam("scores", [scores[c] for c in ids], type_=ARRAY(_SCORE)),
        )
        .table_valued(sa.column("chunk_id", sa.Uuid()), sa.column("score", _SCORE))
        .render_derived(name="scored", with_types=False)
    )
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            chunks.c.ordinal,
            scored.c.score,
            documents.c.external_id,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(scored)
        .join(chunks, chunks.c.chunk_id == scored.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(eligibility)
        .order_by(scored.c.score.desc(), chunks.c.chunk_id)
        .limit(limit)
    )


def sparse_branch(
    runtime: DatabaseRuntime, model: SparseModel, index: SparseIndex
) -> BranchSearch:
    """Weight the question with `model`, then search `index`."""

    async def search(request: BranchRequest) -> BranchResult:
        vectors = await model.encode_sparse(
            [request.question],
            request_id=request.request_id,
            timeout_seconds=request.timeout_seconds,
        )
        async with runtime.transaction() as connection:
            return await search_sparse_chunks(
                connection,
                index=index,
                query=vectors[0],
                identity=model.sparse_identity,
                context=request.context,
                limit=request.limit,
            )

    return search


def _batches(texts: Sequence[str], model: SparseModel) -> list[tuple[int, int]]:
    """Cut `texts` into consecutive (start, end) runs within the model's limits."""
    limits = model.limits
    runs: list[tuple[int, int]] = []
    start = 0
    while start < len(texts):
        end, total = start, 0
        while end < len(texts) and end - start < limits.max_inputs:
            tokens = model.count_tokens(texts[end])
            if end > start and total + tokens > limits.max_batch_tokens:
                break
            total += tokens
            end += 1
        runs.append((start, end))
        start = end
    return runs


def _checked_weights(
    value: object, vocabulary_size: int, error: type[SparseSearchError]
) -> SparseVector:
    """Return `value` if it keeps the sparse port's rules, else raise `error`."""
    if type(value) is not tuple:
        raise error("weights must be a tuple of (token, weight) pairs")
    previous = -1
    for pair in cast(tuple[object, ...], value):
        if type(pair) is not tuple or len(cast(tuple[object, ...], pair)) != 2:
            raise error("weights must be a tuple of (token, weight) pairs")
        token, weight = cast(tuple[object, object], pair)
        if type(token) is not int or not 0 <= token < vocabulary_size:
            raise error("every token must be an int in the vocabulary")
        if (
            type(weight) is not float
            or not math.isfinite(weight)
            or not 0 < weight <= MAX_SPARSE_WEIGHT
        ):
            raise error(f"every weight must be above 0 and at most {MAX_SPARSE_WEIGHT}")
        if token <= previous:
            raise error("tokens must be in ascending order, each once")
        previous = token
    return cast(SparseVector, value)


def _scores(index: SparseIndex, query: SparseVector) -> dict[ChunkId, float]:
    """Sum weight products over shared tokens, in a fixed order every time."""
    scores: dict[ChunkId, float] = defaultdict(float)
    for token, weight in query:
        for chunk_id, chunk_weight in index.postings.get(token, ()):
            scores[chunk_id] += weight * chunk_weight
    return dict(scores)


def _candidate_from(record: Mapping[str, object], *, rank: int) -> SparseCandidate:
    return SparseCandidate(
        rank=rank,
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        source_id=SourceId(cast(UUID, record["source_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        score=cast(float, record["score"]),
        external_id=cast(str, record["external_id"]),
        title=cast(str, record["title"]),
        heading_path=cast("str | None", record["heading_path"]),
        source_anchor=cast("str | None", record["source_anchor"]),
        text=cast(str, record["text"]),
    )
```

Check: `make typecheck` reports `0 errors`, and `make test-unit` still passes: adding an enum member breaks no code that iterates `RetrievalBranch`.

### Step 7 — Coding step: compare both searches and report the cost

This step runs the seed cases with PostgreSQL words and with sparse weights, and writes the report.

**In plain words:** The comparison stores the seed corpus once in an empty database, builds the sparse index, and runs every case twice: once with PostgreSQL word search and once with sparse search. It times every case. The report then shows the two runs side by side per slice, what the index costs, and how long a question takes. The script gets a `--sparse` option that does all this with the local server.

| Piece | What it does |
|---|---|
| `CaseOutcome.elapsed_seconds` | wall time of one case; left out of comparisons between outcomes, because it varies |
| `DatasetReport.sparse` | names the sparse model in the report's configuration |
| `SparseComparison` | both reports, the index size, and its build time |
| `compare_sparse` | ingest, index (a failure becomes `CorpusNotEmbedded`, naming types only), run lexical, run sparse |
| `render_sparse_comparison` | the side-by-side report, then "## Cost", then "## Latency" last |
| `--sparse` | runs it with the local server and writes `artifacts/evaluations/bilingual-seed-sparse.md` |

In `src/knowledge_service/retrieval/evaluation_dataset.py`:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  ones included, is sent to the embedding model's provider.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Replace with:

```python
  ones included, is sent to the embedding model's provider.
- **Compared with sparse weights.** `compare_sparse` indexes every Chunk
  with a sparse model such as BGE-M3 (see `retrieval.sparse_search`), then
  runs every case twice: PostgreSQL word search, and sparse search alone.
  It also returns what the index costs and how long each case took, which
  `render_sparse_comparison` adds to the report. This is the M05-T10
  prototype; nothing in production uses it.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
import re
from collections import Counter
```

Replace with:

```python
import re
import statistics
import time
from collections import Counter
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
)
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
```

Replace with:

```python
)
from knowledge_service.embeddings.sparse import SparseModel, SparseModelIdentity
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
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
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    sparse_branch,
)
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    degraded: bool

```

Replace with:

```python
    degraded: bool
    # Wall time of the search, in seconds; not compared, as it varies by run.
    elapsed_seconds: float = field(default=0.0, compare=False)

```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    embedding: EmbeddingModelIdentity | None = None

```

Replace with:

```python
    embedding: EmbeddingModelIdentity | None = None
    # The model behind the sparse branch; None when the run had none.
    sparse: SparseModelIdentity | None = None


@dataclass(frozen=True, slots=True)
class SparseComparison:
    """Word search and sparse search on the same corpus, and what each costs.

    `chunks` and `weights` count the indexed Chunks and their stored
    (token, weight) pairs; `index_seconds` is the time to build the index.
    """

    lexical: DatasetReport
    sparse: DatasetReport
    chunks: int
    weights: int
    index_seconds: float

```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Replace with:

```python

async def compare_sparse(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: SparseModel,
    limit: int = DEFAULT_LIMIT,
) -> SparseComparison:
    """Ingest and index the corpus once, then run every case two ways.

    Every Chunk, restricted ones included, is sent to `model` for its
    weights, because search must be able to score a restricted Chunk for
    the permission check to matter. Raises like `run_dataset`, and
    `CorpusNotEmbedded` when the corpus cannot be indexed.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=None)
    started = time.perf_counter()
    failure = "no index"
    index = None
    try:
        index = await build_sparse_index(
            runtime,
            model,
            request_id=uuid5(_REQUEST_IDS, "sparse index"),
            timeout_seconds=EMBEDDING_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # The model is outside code, and its message may quote Chunk text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries the text.
    if index is None:
        raise CorpusNotEmbedded(f"the corpus could not be indexed: {failure}")
    index_seconds = time.perf_counter() - started
    lexical = await _run_cases(
        dataset,
        {RetrievalBranch.LEXICAL: lexical_branch(runtime)},
        limit,
        embedding=None,
    )
    sparse = await _run_cases(
        dataset,
        {RetrievalBranch.SPARSE: sparse_branch(runtime, model, index)},
        limit,
        embedding=None,
        sparse=index.identity,
    )
    return SparseComparison(
        lexical=lexical,
        sparse=sparse,
        chunks=index.chunks,
        weights=index.weights,
        index_seconds=index_seconds,
    )


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    return body + "".join("\n" + render_report(report) for report in reports)

```

Replace with:

```python
    return body + "".join("\n" + render_report(report) for report in reports)


def render_sparse_comparison(comparison: SparseComparison) -> str:
    """Return the side-by-side report, then what the index costs, then timing.

    Everything above "## Latency" depends only on the data and the model, so
    two runs can be compared with `diff`; the latency section changes from
    run to run.
    """
    reports = (comparison.lexical, comparison.sparse)
    chunks, weights = comparison.chunks, comparison.weights
    per_chunk = weights / chunks if chunks else 0.0
    # pgvector's sparsevec: 4 bytes of index and 4 of value per weight, plus
    # a 16-byte header per vector.
    stored = 8 * weights + 16 * chunks
    lines = [
        "",
        "## Cost",
        "",
        f"- Indexed Chunks: {chunks}, restricted ones included.",
        f"- Stored weights: {weights}, {per_chunk:.1f} per Chunk on average.",
        f"- As pgvector sparsevec: about {stored / 1024:.1f} KiB for this corpus.",
        "- Per question: word search runs one SQL statement; sparse search"
        " also calls the model once, so it needs the BGE-M3 server running.",
        "",
        "## Latency",
        "",
        "Wall time per case, from the question to the ranked Candidates. It"
        " changes from run to run.",
        "",
        f"- Index build: {comparison.index_seconds:.1f} s.",
        "",
        "| Run | Median ms | Slowest ms |",
        "|---|---:|---:|",
    ]
    for report in reports:
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {_label(report)} | {median:.1f} | {slowest:.1f} |")
    return render_comparison(reports) + "\n".join(lines) + "\n"

```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
            f" {model.dimensions} dimensions."
        )
```

Replace with:

```python
            f" {model.dimensions} dimensions."
        )
    sparse = next((r.sparse for r in reports if r.sparse is not None), None)
    if sparse is not None:
        lines.append(
            f"- Sparse model: {sparse.provider} {sparse.model},"
            f" vocabulary of {sparse.vocabulary_size} tokens."
        )
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
    )


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    result: HybridRetrieval | None = None
    unexpected = "no result"
```

Replace with:

```python
    sparse: SparseModelIdentity | None = None,
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
        sparse=sparse if RetrievalBranch.SPARSE in searches else None,
    )


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    result: HybridRetrieval | None = None
    unexpected = "no result"
    started = time.perf_counter()
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        unexpected = _error_names(error)
    # Raised outside the `except`, so no linked error carries the Question.
```

Replace with:

```python
        unexpected = _error_names(error)
    elapsed = time.perf_counter() - started
    # Raised outside the `except`, so no linked error carries the Question.
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        degraded=result.degraded,
    )
```

Replace with:

```python
        degraded=result.degraded,
        elapsed_seconds=elapsed,
    )
```

In `scripts/run_retrieval_dataset.py`:

Find in `scripts/run_retrieval_dataset.py`:

```python
text leaves the machine.
"""
```

Replace with:

```python
text leaves the machine.

`--sparse` is the M05-T10 prototype. It asks the same local server for
BGE-M3's sparse term weights, runs every case with PostgreSQL word search
and with sparse search, and writes quality, cost, and latency to
`artifacts/evaluations/bilingual-seed-sparse.md`.
"""
```

Find in `scripts/run_retrieval_dataset.py`:

```python
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
```

Replace with:

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

Find in `scripts/run_retrieval_dataset.py`:

```python
    embeddings.add_argument("--local-embeddings", action="store_true")
    arguments = parser.parse_args()
```

Replace with:

```python
    embeddings.add_argument("--local-embeddings", action="store_true")
    embeddings.add_argument("--sparse", action="store_true")
    arguments = parser.parse_args()
```

Find in `scripts/run_retrieval_dataset.py`:

```python
        rendered = asyncio.run(_run(runtime, dataset, corpus, arguments.limit, source))
```

Replace with:

```python
        if arguments.sparse:
            default = SPARSE_REPORT
            rendered = asyncio.run(
                _run_sparse(runtime, dataset, corpus, arguments.limit)
            )
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
```

Find in `scripts/run_retrieval_dataset.py`:

```python

def _openai_model(api_key: SecretStr) -> ModelSource:
```

Replace with:

```python

async def _run_sparse(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> str:
    try:
        async with _local_model() as model:
            comparison = await compare_sparse(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                limit=limit,
            )
        return render_sparse_comparison(comparison)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
```

Find in `scripts/run_retrieval_dataset.py`:

```python
async def _local_model() -> AsyncGenerator[EmbeddingModel]:
```

Replace with:

```python
async def _local_model() -> AsyncGenerator[LocalEmbeddingModel]:
```

Check: `make typecheck` reports `0 errors`, and `uv run python scripts/run_retrieval_dataset.py --sparse --managed-embeddings` prints `not allowed with argument`.

### Step 8 — Coding step: test the search and the comparison

This step proves the search's promises: some without a database, the rest on real PostgreSQL.

**In plain words:** The first file checks everything that must be refused before PostgreSQL is touched: a bad limit, weights from another model, a malformed query or index, and a weight too large to add up safely. Its fake connection fails the test if it is used. The second file runs the whole seed comparison with the fake sparse model, then builds the most dangerous case: a question that matches a restricted Chunk best. Without the permission check that Chunk comes first; with it, it never appears. It also checks that a model failing while indexing stops the run with a typed, quiet error.

| Test | Proves |
|---|---|
| `test_the_index_keeps_postings_by_token_in_chunk_order` | postings and counts |
| `test_a_bad_limit_is_refused_before_the_database`, `test_weights_from_another_model_are_refused_before_the_database`, `test_a_malformed_query_is_refused_before_the_database` | typed refusals before any read |
| `test_the_index_refuses_weights_the_port_does_not_allow`, `test_a_weight_too_large_to_multiply_safely_is_refused` | NaN, negative, huge, unordered, or out-of-vocabulary weights never reach the index or a sum |
| `test_every_case_runs_with_words_and_with_sparse_weights_quietly` | success: both runs, batches within limits, no Question or restricted text in report or logs |
| `test_the_best_scored_chunk_is_restricted_and_still_never_returned` | the highest-risk edge |
| `test_a_model_that_fails_while_indexing_stops_the_run_quietly` | the typed failure: `CorpusNotEmbedded`, with no linked error |

Create `tests/test_sparse_search.py`:

```python
"""Sparse search without a database: the index, and refusals before any read."""

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from fakes import FAKE_VOCABULARY
from knowledge_service.access import AuthorizationContext
from knowledge_service.embeddings.port import EmbeddingMalformedResponse
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)
from knowledge_service.identifiers import ChunkId, UserId
from knowledge_service.retrieval.sparse_search import (
    InvalidSparseQuery,
    InvalidSparseWeights,
    SparseIndexMismatch,
    search_sparse_chunks,
    sparse_index,
)

IDENTITY = SparseModelIdentity(
    provider="fake", model="fake-sparse", vocabulary_size=FAKE_VOCABULARY
)
READER = AuthorizationContext(user_id=UserId(UUID(int=1)))
FIRST = ChunkId(UUID(int=1))
SECOND = ChunkId(UUID(int=2))


class NoDatabase:
    """Fails the test if a search touches the database."""

    async def execute(self, *_: object) -> None:
        raise AssertionError("the database was used")


NO_DATABASE = cast(AsyncConnection, NoDatabase())


def test_the_index_keeps_postings_by_token_in_chunk_order() -> None:
    index = sparse_index(
        IDENTITY,
        {SECOND: ((5, 0.5), (9, 0.25)), FIRST: ((5, 1.0),)},
    )

    assert index.postings == {5: ((FIRST, 1.0), (SECOND, 0.5)), 9: ((SECOND, 0.25),)}
    assert (index.chunks, index.weights) == (2, 3)


def test_valid_sparse_weights_become_pairs_and_may_be_empty() -> None:
    vectors = checked_sparse_vectors(
        [[[3, 0.5], [7, 1.5]], []], expected=2, vocabulary_size=10
    )

    assert vectors == (((3, 0.5), (7, 1.5)), ())


def test_a_wrong_number_of_sparse_vectors_is_refused() -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_sparse_vectors([[[3, 0.5]]], expected=2, vocabulary_size=10)


@pytest.mark.parametrize(
    "limit", [0, 101, True, 1.0], ids=["zero", "too many", "bool", "float"]
)
async def test_a_bad_limit_is_refused_before_the_database(limit: object) -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.0),),
            identity=IDENTITY,
            context=READER,
            limit=cast(int, limit),
        )


async def test_weights_from_another_model_are_refused_before_the_database() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})
    other = SparseModelIdentity(
        provider="local", model="BAAI/bge-m3@other", vocabulary_size=FAKE_VOCABULARY
    )

    with pytest.raises(SparseIndexMismatch):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.0),),
            identity=other,
            context=READER,
            limit=10,
        )


@pytest.mark.parametrize(
    "query",
    [
        [(5, 1.0)],
        ((5, -1.0),),
        ((5, float("nan")),),
        ((FAKE_VOCABULARY, 1.0),),
        ((5, 1.0), (5, 2.0)),
        ((5, 1),),
        ((5,),),
    ],
    ids=[
        "a list",
        "negative weight",
        "not a number",
        "outside the vocabulary",
        "repeated token",
        "int weight",
        "no weight",
    ],
)
async def test_a_malformed_query_is_refused_before_the_database(
    query: object,
) -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=cast(SparseVector, query),
            identity=IDENTITY,
            context=READER,
            limit=10,
        )


async def test_a_query_sharing_no_token_finds_nothing_without_the_database() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    result = await search_sparse_chunks(
        NO_DATABASE,
        index=index,
        query=((6, 1.0),),
        identity=IDENTITY,
        context=READER,
        limit=10,
    )

    assert (result.candidates, result.truncated) == ((), False)


@pytest.mark.parametrize(
    "vector",
    [
        ((5, float("nan")),),
        ((5, -3.0),),
        ((5, 1e300),),
        ((FAKE_VOCABULARY, 1.0),),
        ((9, 1.0), (5, 1.0)),
    ],
    ids=["not a number", "negative", "huge", "outside the vocabulary", "unordered"],
)
def test_the_index_refuses_weights_the_port_does_not_allow(vector: object) -> None:
    with pytest.raises(InvalidSparseWeights):
        sparse_index(IDENTITY, {FIRST: cast(SparseVector, vector)})


async def test_a_weight_too_large_to_multiply_safely_is_refused() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.7e308),),
            identity=IDENTITY,
            context=READER,
            limit=10,
        )


def test_a_weight_over_the_bound_is_malformed() -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_sparse_vectors([[[3, 1e6]]], expected=1, vocabulary_size=10)
```

Create `tests/test_sparse_search_integration.py`:

```python
"""The sparse prototype on real PostgreSQL: the seed run, eligibility, failure."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from fakes import FakeSparseModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits, EmbeddingUnavailable
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    CorpusNotEmbedded,
    EvaluationDataset,
    Folder,
    compare_sparse,
    load_dataset,
    render_sparse_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    search_sparse_chunks,
    sparse_search_statement,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
REQUEST_ID = UUID(int=0x5EED_0010)
# Small batches, so indexing the 21 seed Chunks takes several requests.
LIMITS = EmbeddingLimits(max_inputs=4, max_input_tokens=1_000, max_batch_tokens=4_000)


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
async def test_every_case_runs_with_words_and_with_sparse_weights_quietly(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeSparseModel(limits=LIMITS)

    comparison = await compare_sparse(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )

    assert comparison.lexical.branches == (RetrievalBranch.LEXICAL,)
    assert comparison.sparse.branches == (RetrievalBranch.SPARSE,)
    assert comparison.sparse.sparse == model.sparse_identity
    assert comparison.chunks == 21
    assert comparison.weights > 0
    # Every Chunk was indexed, in batches the model's limits allow.
    assert sum(len(batch) for batch in model.batches) == 21 + len(dataset.cases)
    assert all(len(batch) <= LIMITS.max_inputs for batch in model.batches)
    sparse = {o.case_id: o.ranks for o in comparison.sparse.outcomes}
    # The fake shares a token per shared word, so a code is found by itself.
    assert sparse["en-en-error-vpn-4031"] == (1,)
    assert all(o.elapsed_seconds > 0 for o in comparison.sparse.outcomes)
    rendered = render_sparse_comparison(comparison)
    assert "## Cost" in rendered
    assert "## Latency" in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_best_scored_chunk_is_restricted_and_still_never_returned(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeSparseModel(limits=LIMITS)
    await compare_sparse(runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model)
    index = await build_sparse_index(
        runtime, model, request_id=REQUEST_ID, timeout_seconds=5.0
    )
    async with runtime.transaction() as connection:
        restricted_text = await connection.scalar(
            sa.select(chunks.c.text)
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
            .order_by(chunks.c.ordinal)
            .limit(1)
        )
    assert restricted_text is not None
    # The query is the restricted Chunk's own text: every one of its tokens
    # matches, so no other Chunk can score higher.
    (query,) = await model.encode_sparse(
        [restricted_text], request_id=REQUEST_ID, timeout_seconds=5.0
    )
    scores = {
        chunk_id: sum(
            weight * dict(index.postings.get(token, ())).get(chunk_id, 0.0)
            for token, weight in query
        )
        for chunk_id in {c for pairs in index.postings.values() for c, _ in pairs}
    }
    scores = {chunk_id: score for chunk_id, score in scores.items() if score > 0}

    async with runtime.transaction() as connection:
        unfiltered = (
            (
                await connection.execute(
                    sparse_search_statement(
                        scores=scores, eligibility=sa.true(), limit=1
                    )
                )
            )
            .mappings()
            .one()
        )
        found = await search_sparse_chunks(
            connection,
            index=index,
            query=query,
            identity=model.sparse_identity,
            context=READER,
            limit=21,
        )

    # Without the permission check the restricted Chunk ranks first ...
    assert unfiltered["source_id"] == SOURCE_IDS[Folder.RESTRICTED].value
    # ... with it, no restricted Chunk is ranked, counted, or returned.
    assert found.candidates
    assert {c.source_id for c in found.candidates} == {SOURCE_IDS[Folder.PUBLIC]}
    assert [c.rank for c in found.candidates] == list(
        range(1, len(found.candidates) + 1)
    )
    assert found.truncated is False
    assert all(RESTRICTED_MARKER not in c.text for c in found.candidates)


@pytest.mark.integration
async def test_a_model_that_fails_while_indexing_stops_the_run_quietly(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeSparseModel(limits=LIMITS, error=EmbeddingUnavailable())

    with pytest.raises(CorpusNotEmbedded) as raised:
        await compare_sparse(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert "EmbeddingUnavailable" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    # One batch was tried, and no case ran.
    assert len(model.batches) == 1
```

Check, narrow tests first:

```bash
uv run pytest tests/test_sparse_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_sparse_search_integration.py -q
```

Expected: `23 passed`, then `3 passed`.

### Step 9 — No coding in this step: run, compare, and decide

This step runs the real model, checks that the run repeats, breaks the permission check on purpose, and asks you to decide.

**Action 1:** start the server in one terminal and leave it running:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

If port 8765 is taken, add `BGE_M3_PORT=8766` in front, and in the second terminal run `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766` first. The setting is read from the shell, not from `.env`.

**Action 2:** in a second terminal, run twice on fresh databases and compare everything above "## Latency":

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --sparse
cp artifacts/evaluations/bilingual-seed-sparse.md /tmp/first-sparse.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --sparse > /dev/null
diff <(sed '/^## Latency/,$d' /tmp/first-sparse.md) <(sed '/^## Latency/,$d' artifacts/evaluations/bilingual-seed-sparse.md) && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Each run takes a few seconds once the model is loaded.

**Action 3:** read the report and write the decision. In the evidence file, under "Decision", write:

- the per-slice table from the report (lexical and sparse, Recall@10 and MRR), because `artifacts/` is ignored by Git;
- the "Cost" and "Latency" sections;
- **keep** or **reject**, in one sentence, and three short reasons: quality (which slices, which numbers), latency and cost, and resilience (what happens when the model server is down);
- what "keep" would mean next, if you keep it: for example, store weights as pgvector `sparsevec` behind a migration, or ask the server for dense and sparse in one pass.

Also write at least one **falsified assumption** in the shape "I assumed X. The report shows Y (case IDs and numbers). So Z is wrong."

**Action 4:** break the permission check on purpose. In `src/knowledge_service/retrieval/sparse_search.py`, in `search_sparse_chunks`, change `eligibility=eligible_chunks_predicate(context),` to `eligibility=sa.true(),`. Then run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_sparse_search_integration.py -q
```

Expected: 2 failed, `test_every_case_runs_with_words_and_with_sparse_weights_quietly` and `test_the_best_scored_chunk_is_restricted_and_still_never_returned`. Both stop with `IneligibleChunkRetrieved`: the comparison itself notices the first restricted Chunk that comes back. Undo the change and run again until all pass. Stop the server with Ctrl+C.

### Step 10 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the architecture text the change made incomplete, and records the result.

**Action 1:** in `docs/architecture.md`:

Find in `docs/architecture.md`:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings on Python 3.12, on
127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
```

Replace with:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_sparse_search.py tests/test_local_embeddings.py tests/test_embedding_model_contract.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make ml-check
make docs-check
```

Expected: all pass. The reference run gave 91 passed (1 deselected) for the narrow tests, `make test-unit` 1278 passed, `make test-integration` 303 passed, and `make ml-check` 29 passed.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why BGE-M3 sparse and not a separate engine; why the index lives in memory; how the permission check stays in the same statement; why every weight is checked and bounded.
- **Verification:** the commands and counts, the repeatability result, the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review of the prototype found no broken guarantee and seven smaller problems. Four were fixed, each with a regression test that failed before the fix: weights up to 1.7e308 summed to infinity and tied every Chunk, so weights are now at most 1000; the index trusted the model's vectors, so it now checks them like a query; one answer could carry 250,002 weights for a one-word text, so a text may now have no more weights than tokens; a 1 MB compressed answer took about 145 MB of memory while it was unpacked, before the 8 MB cap was checked, so compressed answers are refused unread. Three are known limits: a sparse branch error propagates instead of degrading a hybrid search, a batch pads every text to its longest one, and the sparse log record names the dense identity.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T10 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T11, and commit with `m05-t10: compare a multilingual sparse path`.

## Reflection questions

1. What does `search_sparse_chunks` hide from the code that calls it? List what a caller never has to think about, such as postings, the `unnest` table, or the order in which scores are added.
2. Someone later speeds the search up by ranking in Python and asking PostgreSQL only for the text of the top 10. What would go wrong with a restricted Chunk, and with `truncated`?
3. The weights are checked twice: in the adapter, and again when the index is built. Which bug or attack does the second check stop that the first does not?
4. Which of your predictions was wrong, and what did you believe about word search or about BGE-M3 that made you predict it?
5. If you keep sparse search, what would have to be true before it replaces PostgreSQL word search for real users? Name one metric and one failure case to test.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] The server pins `sparse_linear.pt`, serves `/v1/sparse` with the same limits, and refuses bad weights; `make ml-check` passes.
- [ ] `SparseModel` and `checked_sparse_vectors` exist, and `LocalEmbeddingModel.encode_sparse` refuses other provenance, bad weights, too many weights, and compressed answers.
- [ ] `search_sparse_chunks` keeps the permission check in the same SQL statement as the ranking; the restricted best match is never returned.
- [ ] Bad limits, other models, and bad weights are refused before PostgreSQL is used.
- [ ] No planted text appears in errors, linked errors, logs, server answers, or reports.
- [ ] You ran `--sparse` twice on fresh databases, and everything above "## Latency" was the same.
- [ ] The evidence file holds the per-slice table, the cost and latency, a keep or reject decision with reasons, and a falsified assumption.
- [ ] You ran the deliberate break from Step 9, saw two tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, ML, docs.
- [ ] The architecture text, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
