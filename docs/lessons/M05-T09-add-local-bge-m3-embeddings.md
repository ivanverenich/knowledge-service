# M05-T09 — Add local BGE-M3 embeddings

Source task: [M05-T09](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T09-add-local-bge-m3-embeddings.md)

## What you will do

In M05-T08 you measured search by meaning with OpenAI's embedding model, which runs on someone else's servers. In this lesson you run an open model, BGE-M3, on your own machine, plug it into the same port, and run the same 16 questions with it. At the end you can compare the local model with the managed one slice by slice, and no text leaves your machine.

- A small **BGE-M3 server** in `ml/bge_m3_server/` runs the model on Python 3.12, as its own project. It answers one HTTP request with one batch of vectors.
- The server pins one exact version of the model weights and checks their fingerprints before it serves anything.
- `LocalEmbeddingModel`, the application's side, talks to that server and keeps every promise of the `EmbeddingModel` port. It refuses vectors from any other model version.
- The same contract tests that cover the fake and OpenAI adapters now cover the local one too.
- The dataset script gets a `--local-embeddings` option, and you compare its report with the managed report from M05-T08.

| The task asks for | Where it happens |
|---|---|
| A Python 3.12 local embedding adapter | Steps 2 to 4: the server project, the server, its tests |
| Model provenance | Step 3: pinned revision and file fingerprints; Step 5: the identity names the revision, and every answer must match it |
| Batching | Step 3: the server's batch limits and encoding batch size; Step 5: limits that make the existing batch planner split work the server accepts |
| An application-facing transport that satisfies the existing port | Step 5: `LocalEmbeddingModel` over HTTP; Step 6: the shared contract suite |
| Deterministic tests for success, the highest-risk edge, one typed failure | Step 4 and Step 6: vectors from other weights are refused; a stopped server is `EmbeddingUnavailable` |
| Evaluation compares local and managed embeddings | Step 7: `--local-embeddings`; Step 8: the run and the side-by-side table |
| No seeded sensitive text in telemetry | Steps 4 and 6: errors, logs, and server answers are checked for a planted text |

This lesson includes coding: Steps 2 to 7 write code. Steps 1, 8, and 9 do not involve code. Step 8 runs the real model on your machine; it costs nothing, but the first start needs the 2.3 GB model files (already in your Hugging Face cache on this machine).

Terms used in this lesson:

- **Weights:** the numbers a trained model is made of. A model name such as `BAAI/bge-m3` can point to new weights tomorrow; a **revision** is the fixed ID of one exact set, like a Git commit.
- **Provenance:** where something came from. Here: which model, which revision, and which files produced a vector.
- **File fingerprint (SHA-256 hash):** a short code computed from a file's bytes. Change one byte and the code changes, so a matching code proves the file is the expected one.
- **Token:** the piece of text a model reads at a time, often part of a word. BGE-M3 accepts at most 8192 tokens per text.
- **Tokenizer:** the program that cuts a text into tokens. Before cutting, it **normalizes** the text, for example turning the single character "ﬁ" into "fi".
- **Truncation:** cutting off the end of a text that is too long. A model that truncates silently embeds only the start, and nobody notices.
- **Loopback (127.0.0.1):** the address that only your own machine can reach.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** The `EmbeddingModel` port in `src/knowledge_service/embeddings/port.py` promises the same things for every model: one call is one batch, input is checked before anything leaves, errors carry no text. `OpenAIEmbeddingModel` keeps those promises for OpenAI. `tests/test_embedding_model_contract.py` checks them for every adapter. `compare_branches` and `scripts/run_retrieval_dataset.py` from M05-T08 run the 16 questions with any model.
- **Why a separate process on Python 3.12.** The application runs on Python 3.14. ML libraries such as PyTorch support new Python versions later, so ADR 0009 keeps them in their own Python 3.12 environment, reached over the network. The application never imports PyTorch, and the ML project never imports the application.
- **Why HTTP, and why only on 127.0.0.1.** HTTP is the simplest network seam, and `httpx` is already in the project. The server has no login, so it listens on loopback only: other machines cannot reach it.
- **How provenance works.** The server downloads one fixed revision of `BAAI/bge-m3` and checks the fingerprints of the weights and tokenizer files before it starts. Every answer names the model, the revision, and the vector size. The application puts the revision into the model's identity, `BAAI/bge-m3@<revision>`, so vectors from other weights get their own Embedding Space. It refuses any answer that names something else.
- **The highest-risk edge.** Suppose someone restarts the server with different weights. Its vectors look normal: 1024 numbers each. If the application stored them in the pinned model's Embedding Space, search would compare vectors from two models, and the ranking would quietly become nonsense. The provenance check in every answer stops that. A test plays a server that answers with another model, revision, or size, and expects `EmbeddingRequestRefused`.
- **Counting tokens without the tokenizer.** The port checks every text's tokens before sending, but the tokenizer lives in the ML project. So the application uses a safe upper bound: the length of the text after Unicode normalization, plus three tokens the tokenizer always adds. Every token covers at least one normalized character, so the real count is never higher. Two normalizations are measured, NFKC and NFKD, and the longer one wins. The tokenizer joins some characters but keeps others apart, such as Korean typed as separate letters, so NFKC alone would count too low. The server's tests check this bound against every Unicode character with the real tokenizer. The bound is pessimistic, about three times the real count for plain text, so batches are smaller than they could be; that only costs a few more requests.
- **Never truncate.** The server counts tokens exactly and refuses a text over 8192 tokens or a request over 16,384. A refusal is visible; a silently shortened text is not.
- **What the server is protected against.** It refuses texts longer than any 8192-token text can be, before counting. It counts outside the request loop, so one large request does not freeze the others. It replaces FastAPI's default validation error, which would repeat the request's text.
- **What the application is protected against.** It reads at most 8 MB of an answer. It checks the URL setting at start-up. The script creates its HTTP client with `trust_env=False`, so a proxy set in the environment never sees the texts.
- **Known limits.** Only dense vectors are used; BGE-M3's sparse output belongs to M05-T10. The server runs one batch at a time on the CPU by default. It has no login and does not check the Host header, so a web page could make it do work, though not read anything. The token bound is checked by brute force, not proven. The server ignores the request ID.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write down what must be true when the lesson is done and what you expect the local model to do. Predictions written now, before the run, are what Step 8 checks.

Create `docs/evidence/M05-T09-local-bge-m3-embeddings.md` with this skeleton, then replace the prediction prompts with your own sentences:

```markdown
# M05-T09 — Local BGE-M3 embeddings evidence

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

- Cross-language slices (`en->uk`, `uk->en`) compared with OpenAI: ...
- The bare identifier "VPN-4031": ...
- Two runs of the local model: the same report or not, and why: ...
- Where word search plus meaning search (fused) helps or hurts: ...

## Design

## Verification

## Reflection
```

Check: the file exists, and every prediction is a full sentence that a number in Step 8 can prove wrong.

### Step 2 — Coding step: create the Python 3.12 server project

This step creates the separate Python 3.12 project the model will run in.

**In plain words:** The model needs PyTorch and a library that knows how to run BGE-M3. Those must not enter the application's Python 3.14 environment. So this step creates a small project of its own, with its own Python version, its own dependencies, and its own lint and type settings. Once it is installed, the next step can write the server inside it.

| Part | What it is for |
|---|---|
| `requires-python = ">=3.12,<3.13"` | the ML runtime from ADR 0009 |
| `sentence-transformers`, `torch` | load and run BGE-M3 |
| `fastapi`, `uvicorn` | the HTTP server |
| `[tool.ruff]` with `py312` | the root `make lint` checks this folder too; without it, ruff would rewrite code into Python 3.14 syntax that 3.12 cannot run |
| `package = false` | the project is a folder of scripts, not an installable package |

Create `ml/bge_m3_server/pyproject.toml`:

```toml
[project]
name = "bge-m3-server"
version = "0.1.0"
description = "Local BGE-M3 embedding server for the knowledge service (ML runtime)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sentence-transformers>=5",
    "torch>=2.6",
]

[dependency-groups]
dev = ["httpx>=0.28", "pyright", "pytest>=8"]

[tool.uv]
package = false

[tool.ruff]
# This runtime is Python 3.12 (ADR 0009); the application's 3.14 syntax is not allowed.
target-version = "py312"
line-length = 88

[tool.ruff.lint]
select = ["A", "ASYNC", "B", "C4", "E", "F", "I", "PTH", "PT", "RET", "RUF", "S", "SIM", "T20", "UP", "W"]

[tool.ruff.lint.per-file-ignores]
"test_*.py" = ["S101"]

[tool.pyright]
typeCheckingMode = "standard"
pythonVersion = "3.12"
venvPath = "."
venv = ".venv"

[tool.pytest.ini_options]
addopts = ["--strict-markers"]
markers = ["model: needs the pinned BGE-M3 files in the Hugging Face cache"]
```

Then install it. `uv` downloads Python 3.12 if your machine does not have it:

```bash
cd ml/bge_m3_server && uv sync && cd ../..
```

Check: `uv run --project ml/bge_m3_server python -c "import sys, torch; print(sys.version_info[:2], torch.__version__)"` prints `(3, 12)` and a PyTorch version. Commit the new `ml/bge_m3_server/uv.lock` with the lesson; Git already ignores the `.venv` folder inside it.

### Step 3 — Coding step: write the BGE-M3 server

This step writes the server itself, in one file.

**In plain words:** When the server starts, it fetches one fixed version of BGE-M3, checks that every big file is exactly the expected one, and loads the model. Then it answers two kinds of request. One returns which model and version it runs. The other takes a list of texts, counts each text's tokens exactly, refuses anything too long, and returns one vector per text with the model and version named next to them. Nothing it returns or logs ever repeats a text.

| Piece | What it does |
|---|---|
| `REVISION`, `FILE_HASHES` | the pinned weights and the fingerprints of the three large files |
| `Encoder` | what the server needs from a model: count tokens, encode texts; a fake in tests |
| `create_app` | builds the server around one encoder |
| `GET /v1/model` | names the model, revision, and size; needs no text |
| `POST /v1/embeddings` | checks limits, then encodes one batch; refuses with a reason and an index only |
| `_check_limits` | exact token counts, run in a thread so a long text does not block other requests |
| `_valid` | refuses a result with the wrong count, the wrong size, or a NaN |
| `BgeM3Encoder` | the real model through sentence-transformers, unit-length vectors |
| `download_model`, `check_files` | fetch the pinned revision, then check every fingerprint |
| `main` | starts the server on 127.0.0.1, port 8765 by default |

Two decisions are worth knowing before you read the code. Requests are handled one batch at a time, because one batch already uses every CPU core. And the server never truncates: `sentence-transformers` would cut a long text to 8192 tokens silently, so the server counts first and refuses.

Create `ml/bge_m3_server/bge_m3_server.py`:

```python
"""A local BGE-M3 embedding server: the ML runtime side of the local seam.

The knowledge service runs on Python 3.14; ML libraries such as PyTorch
follow their own release cadence, so the model runs here, on Python 3.12, as
a separate process (ADR 0009). The service reaches it over HTTP through
`knowledge_service.embeddings.local_adapter`.

The server promises:

- **Pinned provenance.** It loads exactly one revision of `BAAI/bge-m3` and
  checks the SHA-256 of every weight and tokenizer file before serving.
  Every response names the model, revision, and dimensions, so the client
  can refuse vectors from anything else.
- **Exact limits, never truncation.** It counts each input with the model's
  own tokenizer and refuses a request over 64 inputs, 8192 tokens per input,
  or 16,384 tokens in total, naming only the index. The model would
  otherwise cut a long text silently and embed only its start.
- **Unit-length dense vectors** of 1024 numbers, one per input, in order.
- **No text in errors or logs.** Error bodies hold a reason and an index;
  FastAPI's default validation error, which repeats the input, is replaced.
- **Loopback only.** It has no authentication, so it listens on 127.0.0.1.

Run it from the repository root with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py`.
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

logger = logging.getLogger("bge_m3_server")

MODEL_ID = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DIMENSIONS = 1024
MAX_INPUTS = 64
MAX_INPUT_TOKENS = 8192
MAX_BATCH_TOKENS = 16_384
# No vocabulary piece is longer than 16 characters, so a text within the token
# limit is never longer than this; a longer one is refused before counting.
MAX_INPUT_CHARACTERS = MAX_INPUT_TOKENS * 16
ENCODE_BATCH_SIZE = 16
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
}
MODEL_FILES = [
    *FILE_HASHES,
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "1_Pooling/*",
]


class Encoder(Protocol):
    """The model behind the server; a fake in tests, BGE-M3 in production."""

    def count_tokens(self, text: str) -> int:
        """Count `text` exactly in the model's tokens, special tokens included."""
        ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one unit-length vector per text, in order."""
        ...


class ModelFilesChanged(RuntimeError):
    """A downloaded file does not match its pinned hash; nothing is served."""


class EmbedRequest(BaseModel):
    """The request body: the texts to embed, nothing else."""

    model_config = ConfigDict(extra="forbid", strict=True)

    inputs: list[Annotated[str, Field(max_length=MAX_INPUT_CHARACTERS)]] = Field(
        min_length=1, max_length=MAX_INPUTS
    )


def create_app(encoder: Encoder) -> FastAPI:
    """Build the server around one encoder."""
    app = FastAPI(title="bge-m3-server", docs_url=None, redoc_url=None)
    # One batch at a time: the model uses every core for one batch already.
    busy = asyncio.Lock()

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        # The default handler repeats the input, which may be document text.
        return _refusal(422, "invalid_request")

    @app.get("/v1/model")
    async def model() -> dict[str, object]:
        return _provenance()

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


def _check_limits(encoder: Encoder, inputs: list[str]) -> int | JSONResponse:
    """Return the batch's total tokens, or the refusal of the first bad input."""
    total = 0
    for index, text in enumerate(inputs):
        if not text.strip():
            return _refusal(422, "empty_text", index)
        tokens = _count(encoder, text)
        if tokens is None:
            return _refusal(422, "uncountable_text", index)
        if tokens > MAX_INPUT_TOKENS:
            return _refusal(413, "input_too_long", index)
        total += tokens
        if total > MAX_BATCH_TOKENS:
            return _refusal(413, "batch_too_long", index)
    return total


def _valid(vectors: list[list[float]], expected: int) -> bool:
    # A NaN cannot be written as JSON, and a short vector would be refused later.
    return len(vectors) == expected and all(
        len(vector) == DIMENSIONS and all(math.isfinite(value) for value in vector)
        for vector in vectors
    )


def _count(encoder: Encoder, text: str) -> int | None:
    # A tokenizer error may quote the text, so only its absence is kept.
    try:
        tokens = encoder.count_tokens(text)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens >= 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _provenance() -> dict[str, object]:
    return {"model": MODEL_ID, "revision": REVISION, "dimensions": DIMENSIONS}


def _refusal(status: int, reason: str, index: int | None = None) -> JSONResponse:
    return JSONResponse({"error": reason, "index": index}, status_code=status)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    device = os.environ.get("BGE_M3_DEVICE", "cpu")
    port = int(os.environ.get("BGE_M3_PORT", "8765"))
    encoder = BgeM3Encoder(download_model(), device)
    uvicorn.run(create_app(encoder), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
```

Check: start the server from the repository root and ask it which model it runs:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

When it prints `Uvicorn running on http://127.0.0.1:8765`, run `curl -s 127.0.0.1:8765/v1/model` in another terminal. Expected: `{"model":"BAAI/bge-m3","revision":"5617a9f61b028005a4858fdac845db406aefb181","dimensions":1024}`. Stop the server with Ctrl+C. If the files were not cached yet, the first start downloads 2.3 GB.

### Step 4 — Coding step: test the server

This step tests the server's promises without loading the model.

**In plain words:** Most tests give the server a fake model that counts one token per character, so they run in seconds and need no download. They check that a too-long text is refused and never reaches the model, that a broken request is refused without repeating its text, that a model failure leaks nothing, and that a changed file is caught. One more test uses the real tokenizer to check the application's token bound against every Unicode character; it skips when the model files are not cached.

| Test | Proves |
|---|---|
| `test_every_response_names_the_pinned_model` | success: vectors in order, with model, revision, size, and token count |
| `test_a_request_over_the_limits_is_refused_and_never_truncated` | too long, blank, or uncountable input is refused and never encoded |
| `test_an_invalid_request_is_refused_without_repeating_it` | a wrong body gets `invalid_request`, never the text |
| `test_an_encoding_failure_carries_no_text` | a model that raises or returns NaN gives `encoding_failed`; no text in the answer or the logs |
| `test_a_changed_or_missing_model_file_is_refused` | provenance: a wrong or missing file raises `ModelFilesChanged` |
| `test_the_application_token_bound_is_never_too_low` | the bound in Step 5 is never below the real count |

Create `ml/bge_m3_server/test_bge_m3_server.py`:

```python
"""The BGE-M3 server: limits, provenance, and quiet failures.

Most tests use a fake encoder, so they need neither PyTorch nor the model.
Tests marked `model` use the real tokenizer and skip when the pinned files
are not in the Hugging Face cache.
"""

import logging
import random
import unicodedata
from collections.abc import Sequence
from pathlib import Path

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


def client(encoder: FakeEncoder) -> TestClient:
    return TestClient(server.create_app(encoder), raise_server_exceptions=False)


def test_every_response_names_the_pinned_model() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json={"inputs": ["a", "bc"]})

    body = answer.json()
    assert answer.status_code == 200
    assert (body["model"], body["revision"], body["dimensions"]) == (
        "BAAI/bge-m3",
        server.REVISION,
        1024,
    )
    assert body["input_tokens"] == 3
    assert len(body["vectors"]) == 2
    assert encoder.encoded == [["a", "bc"]]


def test_the_model_endpoint_needs_no_text() -> None:
    answer = client(FakeEncoder()).get("/v1/model")

    assert answer.json() == {
        "model": "BAAI/bge-m3",
        "revision": server.REVISION,
        "dimensions": 1024,
    }


@pytest.mark.parametrize(
    ("inputs", "status", "reason", "index"),
    [
        (["fine", "x" * (server.MAX_INPUT_TOKENS + 1)], 413, "input_too_long", 1),
        (["x" * 8000, "y" * 8000, "z" * 500], 413, "batch_too_long", 2),
        (["fine", "   "], 422, "empty_text", 1),
        (["uncountable"], 422, "uncountable_text", 0),
    ],
    ids=["one input too long", "batch too long", "blank", "uncountable"],
)
def test_a_request_over_the_limits_is_refused_and_never_truncated(
    inputs: list[str], status: int, reason: str, index: int
) -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json={"inputs": inputs})

    assert answer.status_code == status
    assert answer.json() == {"error": reason, "index": index}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "body",
    [
        {"inputs": [PLANTED, 5]},
        {"inputs": []},
        {"inputs": [PLANTED] * (server.MAX_INPUTS + 1)},
        {"inputs": [PLANTED], "extra": PLANTED},
        {"inputs": ["x" * (server.MAX_INPUT_CHARACTERS + 1)]},
        {"texts": [PLANTED]},
    ],
    ids=["not a string", "empty", "too many", "extra field", "too long", "wrong field"],
)
def test_an_invalid_request_is_refused_without_repeating_it(
    body: dict[str, object],
) -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json=body)

    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid_request", "index": None}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "encoder",
    [FakeEncoder(fail=True), FakeEncoder(value=float("nan"))],
    ids=["raises", "not a number"],
)
def test_an_encoding_failure_carries_no_text(
    encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(encoder).post("/v1/embeddings", json={"inputs": [PLANTED]})

    assert answer.status_code == 500
    assert answer.json() == {"error": "encoding_failed", "index": None}
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text
    assert "encoding failed" in caplog.text


def test_a_changed_or_missing_model_file_is_refused(tmp_path: Path) -> None:
    for name in server.FILE_HASHES:
        (tmp_path / name).write_bytes(b"not the pinned weights")

    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)
    (tmp_path / "pytorch_model.bin").unlink()
    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)


def _cached_model() -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        folder = snapshot_download(
            server.MODEL_ID,
            revision=server.REVISION,
            allow_patterns=server.MODEL_FILES,
            local_files_only=True,
        )
    except Exception:
        return None
    return Path(folder)


@pytest.mark.model
def test_the_application_token_bound_is_never_too_low() -> None:
    # Mirrors LocalEmbeddingModel.count_tokens in the application, which must
    # never count fewer tokens than the server, or a batch it lets through
    # would be refused here.
    folder = _cached_model()
    if folder is None:
        pytest.skip("the pinned BGE-M3 files are not in the Hugging Face cache")
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))

    def bound(text: str) -> int:
        return (
            max(
                len(unicodedata.normalize("NFKC", text)),
                len(unicodedata.normalize("NFKD", text)),
            )
            + 3
        )

    every_character = [
        chr(point) for point in range(0x110000) if not 0xD800 <= point <= 0xDFFF
    ]
    rng = random.Random(9)  # noqa: S311 - a repeatable sample, not a secret
    mixed = [
        "".join(
            rng.choice(every_character) if rng.random() < 0.5 else rng.choice(" \tЇя1-")
            for _ in range(rng.randint(1, 40))
        )
        for _ in range(20_000)
    ]
    # Each character alone, decomposed, and between letters, as text has it.
    in_words = [f"a{character}b {character}" for character in every_character]
    decomposed = [unicodedata.normalize("NFD", text) for text in every_character]
    accented = [
        f"{character}\N{COMBINING ACUTE ACCENT}" * 10 for character in every_character
    ]
    texts = every_character + in_words + decomposed + accented + mixed
    for start in range(0, len(texts), 10_000):
        group = texts[start : start + 10_000]
        encoded = tokenizer.encode_batch(group, add_special_tokens=True)
        assert all(
            len(item.ids) <= bound(text)
            for text, item in zip(group, encoded, strict=True)
        )
```

Add a Makefile target, so the ML checks have one command. In `Makefile`:

Find in `Makefile`:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration check hooks-install hooks docs-check
```

Replace with:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check check hooks-install hooks docs-check
```

Find in `Makefile`:

```makefile
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"
```

Replace with:

```makefile
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

ml-check: ## Type-check and test the local BGE-M3 server (Python 3.12)
	cd ml/bge_m3_server && uv run pyright && uv run pytest -q
```

Makefile recipe lines start with a tab, not spaces.

Check: `make ml-check` reports `0 errors` and `16 passed` (about 20 seconds; the token bound test is the slow one). To see the bound test catch a real mistake, change `"NFKD"` to `"NFKC"` inside its `bound` function: it fails on Korean typed as separate letters. Undo the change.

### Step 5 — Coding step: write the application's adapter

This step writes `LocalEmbeddingModel`, the application's side of the seam.

**In plain words:** The application asks this adapter for vectors exactly as it asks the OpenAI one. The adapter checks the texts against safe limits, sends them to the local server in one request, and reads the answer without trusting it: the status, the size, the shape, and above all the model and version it names. Any problem becomes one of the port's typed errors, and nothing it logs or raises holds a text.

First make `httpx` a runtime dependency; until now only the tests used it. In `pyproject.toml`:

Find in `pyproject.toml`:

```toml
    "fastapi>=0.141.1",
    "markdown-it-py>=4,<5",
```

Replace with:

```toml
    "fastapi>=0.141.1",
    "httpx>=0.28.1",
    "markdown-it-py>=4,<5",
```

Find in `pyproject.toml`:

```toml
dev = [
    "httpx>=0.28.1",
    "hypothesis>=6.170.5",
```

Replace with:

```toml
dev = [
    "hypothesis>=6.170.5",
```

Then run `uv sync`. Only the `httpx` entries move in `uv.lock`; the version stays 0.28.1.

| Piece | What it does |
|---|---|
| `LocalEmbeddingSettings` | `LOCAL_EMBEDDING_*` settings; the URL is checked at start-up |
| `identity` | provider `local`, model `BAAI/bge-m3@<revision>`, 1024 dimensions |
| `limits` | 32 texts, 8192 tokens each, 16,384 per request: within the server's limits |
| `count_tokens` | the safe upper bound from "Before you begin" |
| `embed` | checks input, sends one request, reads the answer, logs counts only |
| `verify_server` | asks `/v1/model` and checks provenance, sending no text |
| `_send` | one request under one deadline, read in pieces up to 8 MB; transport errors become port errors outside the `except` block |
| `_check_provenance` | refuses any answer that names another model, revision, or size; `1024.0` does not pass as `1024` |
| `_check_status` | 429 rate limited, 408/504 timeout, 5xx unavailable, anything else refused |

Create `src/knowledge_service/embeddings/local_adapter.py`:

```python
"""Local BGE-M3 implementation of the provider-neutral EmbeddingModel port.

The model itself runs in a separate Python 3.12 process, the BGE-M3 server
in `ml/bge_m3_server/`, because ML libraries follow a different Python
release cadence than this application (ADR 0009). This adapter is the
application's side of that seam: one call to `embed` is one HTTP request to
the server. It adds what the port cannot know:

- **Pinned provenance.** The identity names the model and the exact weights
  revision, `BAAI/bge-m3@<revision>`, so vectors from other weights never
  share an Embedding Space. Every response must name the same model,
  revision, and dimensions; any other answer raises
  `EmbeddingRequestRefused` and no vector is returned, because storing it
  would mix two models in one space.
- **A safe token bound without a tokenizer.** `count_tokens` returns an
  upper bound, so a batch the server would refuse is refused here first,
  before any text leaves the process. The server counts exactly and refuses
  rather than truncating.
- **Retry classification.** A refused connection or a 5xx becomes
  `EmbeddingUnavailable`, a 429 `EmbeddingRateLimited`, a timeout
  `EmbeddingTimeout`, and any other refusal `EmbeddingRequestRefused`.
- **A bounded answer.** The response is read in pieces and refused past
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory.
- **Safe logging.** One log record per call with counts, timing, and the
  outcome; never text. Port errors are raised outside the `except` block,
  because an HTTP error holds the request, and the request holds every text.

The caller owns the `httpx.AsyncClient`. Create it with `trust_env=False`,
so a proxy set in the environment never receives the texts.
"""

import asyncio
import json
import logging
import math
import time
import unicodedata
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.embeddings.port import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    EmbeddingModelIdentity,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
    EmbeddingUsage,
    check_inputs,
    checked_batch,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
# The BGE-M3 weights this application was evaluated with (Hugging Face commit).
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
# 64 vectors of 1024 numbers as JSON take about 2 MB; far more means a fault.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# <s>, </s>, and the word-start marker the tokenizer adds before the text.
_ADDED_TOKENS = 3


class LocalEmbeddingSettings(BaseSettings):
    """Settings for the local BGE-M3 adapter, read from `LOCAL_EMBEDDING_*`.

    The limits must not exceed the server's: 8192 tokens per input, as the
    model allows, and at most 64 inputs and 16,384 tokens per request.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_EMBEDDING_")

    url: str = Field(default="http://127.0.0.1:8765", min_length=1)
    model: str = Field(default="BAAI/bge-m3", min_length=1)
    revision: str = Field(default=BGE_M3_REVISION, min_length=1)
    dimensions: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_inputs: int = Field(default=32, gt=0)
    max_input_tokens: int = Field(default=8192, gt=0)
    max_batch_tokens: int = Field(default=16_384, gt=0)

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


class LocalEmbeddingModel:
    """Translate EmbeddingModel calls to the local BGE-M3 server."""

    def __init__(
        self, client: httpx.AsyncClient, settings: LocalEmbeddingSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._identity = EmbeddingModelIdentity(
            provider=PROVIDER,
            model=f"{settings.model}@{settings.revision}",
            dimensions=settings.dimensions,
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
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        """Return an upper bound on the text's BGE-M3 tokens.

        The tokenizer first normalizes the text, and every token then covers
        at least one normalized character or the word-start marker. Its
        normalization composes some characters, as NFKC does, but leaves
        others apart, such as Korean typed as separate letters, so the
        longer of the NFKC and NFKD forms is used. The server's tests check
        this bound against every Unicode character with the real tokenizer.
        """
        longest = max(
            len(unicodedata.normalize("NFKC", text)),
            len(unicodedata.normalize("NFKD", text)),
        )
        return longest + _ADDED_TOKENS

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        deadline = self._deadline(timeout_seconds)
        try:
            check_inputs(texts, self._limits, self.count_tokens)
        except EmbeddingInputRejected as error:
            _log(request_id, self._identity, len(texts), 0, error)
            raise
        if not texts:
            return EmbeddingBatch(
                identity=self._identity,
                vectors=(),
                usage=EmbeddingUsage(input_tokens=0, latency_ms=0),
            )
        started_at = time.perf_counter()
        try:
            status, content = await self._send(
                "POST",
                EMBEDDINGS_PATH,
                deadline,
                body={"inputs": list(texts)},
                headers={"X-Request-ID": str(request_id)},
            )
            latency_ms = _since(started_at)
            batch = self._batch_from(status, content, len(texts), latency_ms)
        except EmbeddingError as error:
            _log(request_id, self._identity, len(texts), _since(started_at), error)
            raise
        _log(request_id, self._identity, len(texts), latency_ms, None, batch.usage)
        return batch

    async def verify_server(self, *, timeout_seconds: float) -> None:
        """Check, sending no text, that the server runs the configured model.

        Raises `EmbeddingUnavailable` or `EmbeddingTimeout` when the server
        cannot be reached, and `EmbeddingRequestRefused` when it serves other
        weights. Call it before a long run, so a stopped server or a wrong
        revision fails at once instead of after the first batch.
        """
        deadline = self._deadline(timeout_seconds)
        status, content = await self._send("GET", MODEL_PATH, deadline)
        _check_status(status)
        self._check_provenance(_json_object(content))

    def _deadline(self, timeout_seconds: float) -> float:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
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
        if failure is not None:
            # Raised outside the except block: the HTTP error holds the request.
            raise failure
        return status, bytes(content)

    def _batch_from(
        self, status: int, content: bytes, expected: int, latency_ms: int
    ) -> EmbeddingBatch:
        """Read the response without trusting its status, shape, or provenance."""
        _check_status(status)
        body = self._check_provenance(_json_object(content))
        tokens = body.get("input_tokens")
        if type(tokens) is not int or tokens < 0:
            raise EmbeddingMalformedResponse("the response has no token usage")
        return checked_batch(
            self._identity,
            body.get("vectors"),
            EmbeddingUsage(input_tokens=tokens, latency_ms=latency_ms),
            expected=expected,
        )

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
        if body is None:
            raise EmbeddingMalformedResponse("the local model response was not JSON")
        dimensions = body.get("dimensions")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
            # 1024.0 == 1024 in Python, so the type is checked as well.
            or type(dimensions) is not int
            or dimensions != self._settings.dimensions
        ):
            # Another model's vectors would be stored in this model's space.
            raise EmbeddingRequestRefused(
                "the local model is not the configured model, revision, or size"
            )
        return body


def _check_status(status: int) -> None:
    if status == 429:
        raise EmbeddingRateLimited()
    if status in (408, 504):
        raise EmbeddingTimeout()
    if status >= 500:
        raise EmbeddingUnavailable()
    if status != 200:
        raise EmbeddingRequestRefused(
            f"the local model refused the request with status {status}"
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
    identity: EmbeddingModelIdentity,
    inputs: int,
    latency_ms: int,
    error: EmbeddingError | None,
    usage: EmbeddingUsage | None = None,
) -> None:
    """Record one call: identifiers, counts, timing, and the outcome. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "dimensions": identity.dimensions,
        "inputs": inputs,
        "latency_ms": latency_ms,
        "outcome": "succeeded" if error is None else type(error).__name__,
    }
    if usage is not None:
        fields["input_tokens"] = usage.input_tokens
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "embedding request finished", extra={"embedding": fields})
```

Check: `make typecheck` reports `0 errors`. `uv run python -c "from knowledge_service.embeddings.local_adapter import LocalEmbeddingSettings as S; print(S().url)"` prints `http://127.0.0.1:8765`.

### Step 6 — Coding step: test the adapter

This step proves the adapter keeps the port's promises, using a fake server inside the test process.

**In plain words:** A stand-in server answers through `httpx`'s in-memory transport, so the real adapter sends real HTTP requests that never leave the test. The shared contract suite now runs every one of its checks against the local adapter as well. Separate tests cover what only this adapter has: the provenance check, each kind of transport failure, malformed answers, the size cap, the URL check, and silence about texts.

| Test | Proves |
|---|---|
| contract suite, case `local` | the same six port promises as the fake and OpenAI adapters |
| `test_vectors_from_other_weights_are_refused` | the highest-risk edge: another model, revision, or size, even `1024.0`, raises `EmbeddingRequestRefused` |
| `test_each_failure_becomes_one_typed_error` | a stopped server is `EmbeddingUnavailable` (the typed failure); every status and broken body maps to one port error |
| `test_a_malformed_body_is_refused` | bad token counts and vector lists are refused |
| `test_a_failure_carries_no_text_in_its_chain_or_the_logs` | a planted text never appears in the error, its linked errors, or the log record |
| `test_the_server_check_...` | `verify_server` sends no text, refuses other weights, reports a stopped server, and keeps its deadline |
| `test_an_answer_over_the_size_cap_is_refused_unread` | a valid answer padded past 8 MB is refused |
| `test_a_url_that_is_not_http_is_refused_at_start_up` | a broken URL fails in the settings, not inside a request |
| `test_letters_the_tokenizer_keeps_apart_are_counted_apart` | the bound counts Korean typed as separate letters correctly |

Create `tests/local_stubs.py`:

```python
"""A stand-in for the local BGE-M3 server, for deterministic tests.

`StubBgeM3Server` answers through `httpx.MockTransport`, so the real
`LocalEmbeddingModel` sends real HTTP requests that never leave the process.
"""

import asyncio
import json

import httpx

from knowledge_service.embeddings.local_adapter import (
    BGE_M3_REVISION,
    LocalEmbeddingModel,
    LocalEmbeddingSettings,
)
from openai_stubs import vector_for

URL = "http://bge-m3.test"


class StubBgeM3Server:
    """Answer like the BGE-M3 server; record every request."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes | None = None,
        model: str = "BAAI/bge-m3",
        revision: str = BGE_M3_REVISION,
        dimensions: float = 1024,
        vector_dimensions: int | None = None,
        error: Exception | None = None,
        block: bool = False,
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
        self.requests: list[httpx.Request] = []

    @property
    def sent(self) -> int:
        """How many requests carried texts to embed."""
        return sum(1 for request in self.requests if request.method == "POST")

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._body is not None or self._status != 200:
            return httpx.Response(self._status, content=self._body or b"{}")
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        texts: list[str] = json.loads(request.content)["inputs"]
        return httpx.Response(
            200,
            json={
                **self._provenance,
                "input_tokens": 3 * len(texts),
                "vectors": [vector_for(t, self._vector_dimensions) for t in texts],
            },
        )


def local_embedding_model(
    server: StubBgeM3Server, settings: LocalEmbeddingSettings | None = None
) -> LocalEmbeddingModel:
    """The real adapter, talking to `server` through an in-process transport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    return LocalEmbeddingModel(client, settings or LocalEmbeddingSettings(url=URL))
```

Add the local adapter to the contract suite. In `tests/test_embedding_model_contract.py`:

Find in `tests/test_embedding_model_contract.py`:

```python
from openai_stubs import StubEmbeddings, openai_embedding_model, status_error
```

Replace with:

```python
from local_stubs import StubBgeM3Server, local_embedding_model
from openai_stubs import StubEmbeddings, openai_embedding_model, status_error
```

Find in `tests/test_embedding_model_contract.py`:

```python
    return Adapter(model=model, sent=lambda: len(embeddings.calls))


CASES = (
```

Replace with:

```python
    return Adapter(model=model, sent=lambda: len(embeddings.calls))


def local_adapter(server: StubBgeM3Server) -> Adapter:
    return Adapter(model=local_embedding_model(server), sent=lambda: server.sent)


CASES = (
```

Find in `tests/test_embedding_model_contract.py`:

```python
        blocked=lambda: openai_adapter(StubEmbeddings(block=True)),
    ),
)
```

Replace with:

```python
        blocked=lambda: openai_adapter(StubEmbeddings(block=True)),
    ),
    AdapterCase(
        name="local",
        working=lambda: local_adapter(StubBgeM3Server()),
        rate_limited=lambda: local_adapter(StubBgeM3Server(status=429)),
        wrong_dimensions=lambda: local_adapter(StubBgeM3Server(vector_dimensions=3)),
        blocked=lambda: local_adapter(StubBgeM3Server(block=True)),
    ),
)
```

Create `tests/test_local_embeddings.py`:

```python
"""The local BGE-M3 adapter: provenance, transport errors, and quiet failures."""

import asyncio
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from knowledge_service.embeddings.local_adapter import (
    BGE_M3_REVISION,
    MAX_RESPONSE_BYTES,
    LocalEmbeddingSettings,
)
from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingMalformedResponse,
    EmbeddingModelIdentity,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
)
from local_stubs import URL, StubBgeM3Server, local_embedding_model

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000031")
PLANTED = "the salary of Olena is 9000"


async def embed(server: StubBgeM3Server, texts: list[str]) -> None:
    model = local_embedding_model(server)
    await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=2.0)


def chain(error: BaseException) -> list[BaseException]:
    """The error and every error linked to it, as a traceback would print."""
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        item = pending.pop()
        if item is None or any(item is known for known in seen):
            continue
        seen.append(item)
        pending += [item.__cause__, item.__context__]
    return seen


def test_the_identity_pins_the_weights_revision() -> None:
    model = local_embedding_model(StubBgeM3Server())

    assert model.identity == EmbeddingModelIdentity(
        provider="local",
        model=f"BAAI/bge-m3@{BGE_M3_REVISION}",
        dimensions=1024,
    )


async def test_one_request_carries_the_texts_and_the_request_id() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server)

    batch = await model.embed(
        ["vacation policy", "відпустка"], request_id=REQUEST_ID, timeout_seconds=2.0
    )

    [request] = server.requests
    assert request.url == httpx.URL(f"{URL}/v1/embeddings")
    assert request.headers["X-Request-ID"] == str(REQUEST_ID)
    assert json.loads(request.content) == {"inputs": ["vacation policy", "відпустка"]}
    assert batch.usage.input_tokens == 6


@pytest.mark.parametrize(
    "server",
    [
        StubBgeM3Server(model="BAAI/bge-m3-unsupervised"),
        StubBgeM3Server(revision="0" * 40),
        StubBgeM3Server(dimensions=768, vector_dimensions=1024),
        StubBgeM3Server(dimensions=1024.0),
    ],
    ids=["other model", "other revision", "other dimensions", "dimensions as float"],
)
async def test_vectors_from_other_weights_are_refused(server: StubBgeM3Server) -> None:
    # The highest-risk edge: these vectors would be stored in this model's
    # Embedding Space and silently mixed with vectors they cannot be compared to.
    with pytest.raises(EmbeddingRequestRefused):
        await embed(server, ["vacation policy"])


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (StubBgeM3Server(error=httpx.ConnectError("refused")), EmbeddingUnavailable),
        (StubBgeM3Server(error=httpx.ReadTimeout("slow")), EmbeddingTimeout),
        (StubBgeM3Server(status=503), EmbeddingUnavailable),
        (StubBgeM3Server(status=500), EmbeddingUnavailable),
        (StubBgeM3Server(status=504), EmbeddingTimeout),
        (StubBgeM3Server(status=413), EmbeddingRequestRefused),
        (StubBgeM3Server(status=422), EmbeddingRequestRefused),
        (StubBgeM3Server(status=404), EmbeddingRequestRefused),
        (StubBgeM3Server(body=b"not json"), EmbeddingMalformedResponse),
        (StubBgeM3Server(body=b"[" * 100_000), EmbeddingMalformedResponse),
        (StubBgeM3Server(body=b"[]"), EmbeddingMalformedResponse),
    ],
    ids=[
        "no server",
        "read timeout",
        "busy",
        "server error",
        "gateway timeout",
        "too long",
        "invalid",
        "wrong url",
        "not json",
        "nested too deep",
        "not an object",
    ],
)
async def test_each_failure_becomes_one_typed_error(
    server: StubBgeM3Server, error: type[EmbeddingError]
) -> None:
    with pytest.raises(error):
        await embed(server, ["vacation policy"])


@pytest.mark.parametrize(
    "fields",
    [
        {"input_tokens": True, "vectors": [[1.0] * 1024]},
        {"input_tokens": -1, "vectors": [[1.0] * 1024]},
        {"vectors": [[1.0] * 1024]},
        {"input_tokens": 3, "vectors": "1.0"},
        {"input_tokens": 3, "vectors": [[1.0] * 1024, [1.0] * 1024]},
    ],
    ids=["bool tokens", "negative tokens", "no tokens", "string", "extra vector"],
)
async def test_a_malformed_body_is_refused(fields: dict[str, object]) -> None:
    body = {"model": "BAAI/bge-m3", "revision": BGE_M3_REVISION, "dimensions": 1024}
    server = StubBgeM3Server(body=json.dumps({**body, **fields}).encode())

    with pytest.raises(EmbeddingMalformedResponse):
        await embed(server, ["vacation policy"])


async def test_a_failure_carries_no_text_in_its_chain_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The transport error holds the request, and the request holds the text.
    server = StubBgeM3Server(error=httpx.ConnectError(PLANTED))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmbeddingUnavailable) as raised:
        await embed(server, [PLANTED])

    assert all(PLANTED not in repr(item) for item in chain(raised.value))
    assert raised.value.__context__ is None
    [record] = caplog.records
    assert PLANTED not in caplog.text
    assert PLANTED not in repr(record.__dict__)
    assert record.__dict__["embedding"]["outcome"] == "EmbeddingUnavailable"


async def test_the_server_check_sends_no_text_and_refuses_other_weights() -> None:
    good = StubBgeM3Server()
    other = StubBgeM3Server(revision="0" * 40)

    await local_embedding_model(good).verify_server(timeout_seconds=2.0)
    with pytest.raises(EmbeddingRequestRefused):
        await local_embedding_model(other).verify_server(timeout_seconds=2.0)

    assert [request.method for request in good.requests] == ["GET"]
    assert good.requests[0].url == httpx.URL(f"{URL}/v1/model")


async def test_the_server_check_reports_a_stopped_server() -> None:
    server = StubBgeM3Server(error=httpx.ConnectError("refused"))

    with pytest.raises(EmbeddingUnavailable):
        await local_embedding_model(server).verify_server(timeout_seconds=2.0)


async def test_a_trailing_slash_in_the_url_is_ignored() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server, LocalEmbeddingSettings(url=f"{URL}/"))

    await model.embed(["vacation policy"], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert server.requests[0].url == httpx.URL(f"{URL}/v1/embeddings")


@pytest.mark.parametrize(
    "url", ["http://[::1", "ftp://127.0.0.1:8765", "127.0.0.1:8765", "http://"]
)
def test_a_url_that_is_not_http_is_refused_at_start_up(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalEmbeddingSettings(url=url)


async def test_the_server_check_keeps_the_configured_deadline() -> None:
    server = StubBgeM3Server(block=True)
    settings = LocalEmbeddingSettings(url=URL, timeout_seconds=0.05)
    model = local_embedding_model(server, settings)

    with pytest.raises(EmbeddingTimeout):
        await asyncio.wait_for(model.verify_server(timeout_seconds=math.inf), 2.0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        await model.verify_server(timeout_seconds=math.nan)


def test_letters_the_tokenizer_keeps_apart_are_counted_apart() -> None:
    # Korean typed as separate letters (as macOS stores file names) stays
    # three tokens per syllable; NFKC alone would count it as one.
    model = local_embedding_model(StubBgeM3Server())
    text = unicodedata.normalize("NFD", "\uac01" * 10)

    assert model.count_tokens(text) >= 30 + 3


async def test_an_answer_over_the_size_cap_is_refused_unread() -> None:
    # A valid answer padded past the cap: nothing beyond the cap is trusted.
    valid = {
        "model": "BAAI/bge-m3",
        "revision": BGE_M3_REVISION,
        "dimensions": 1024,
        "input_tokens": 3,
        "vectors": [[1.0] * 1024],
    }
    padded = json.dumps(valid).encode() + b" " * MAX_RESPONSE_BYTES

    with pytest.raises(EmbeddingMalformedResponse, match="too large"):
        await embed(StubBgeM3Server(body=padded), ["vacation policy"])
```

Check, narrow test first:

```bash
uv run pytest tests/test_local_embeddings.py -q
uv run pytest tests/test_embedding_model_contract.py -q
```

Expected: `33 passed`, then `18 passed, 1 deselected` (six checks for each of three adapters; the deselected one is the opt-in live OpenAI test).

### Step 7 — Coding step: add the local option to the script

This step lets you run the 16 questions with the local model from the command line.

**In plain words:** The script gets a second model option next to the OpenAI one. With `--local-embeddings`, it first asks the server which model it runs, so a stopped server or a wrong version fails at once and the database stays empty. Then it runs the same three-way comparison as in M05-T08 and writes the report to its own file.

| Change | Why |
|---|---|
| `--local-embeddings`, exclusive with `--managed-embeddings` | one model per run, one current Embedding Space |
| `ModelSource` and the two `_..._model` functions | each opens and closes its own client around the run |
| `verify_server` before `compare_branches` | a stopped server is reported before anything is written |
| `httpx.AsyncClient(trust_env=False)` | a proxy from the environment never sees the texts |
| `artifacts/evaluations/bilingual-seed-local.md` | a separate report, ignored by Git |
| `except EmbeddingError` | prints `the embedding model is not ready: ...` and exits with 1 |

Replace the whole of `scripts/run_retrieval_dataset.py` with:

```python
"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. By default only word search runs and
the report goes to `artifacts/evaluations/bilingual-seed.md`.

`--managed-embeddings` also embeds the corpus with the OpenAI model set by
`EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` (default
`text-embedding-3-small`, 1536), using `KNOWLEDGE_SERVICE_MODEL_API_KEY`.
It runs every case three ways, words only, meaning only, and both fused, and
writes `artifacts/evaluations/bilingual-seed-managed.md`. Git ignores both
reports. Every Chunk, the restricted ones included, is sent to OpenAI; the
seed corpus is made up, so nothing private leaves the machine.

`--local-embeddings` does the same with the local BGE-M3 server instead
(`LOCAL_EMBEDDING_URL`, default `http://127.0.0.1:8765`), which must already
run the pinned revision; start it with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py`.
The report goes to `artifacts/evaluations/bilingual-seed-local.md`, and no
text leaves the machine.
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.local_adapter import (
    LocalEmbeddingModel,
    LocalEmbeddingSettings,
)
from knowledge_service.embeddings.openai_adapter import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from knowledge_service.embeddings.port import EmbeddingError, EmbeddingModel
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    EvaluationDataset,
    compare_branches,
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
SERVER_CHECK_SECONDS = 10.0

type ModelSource = Callable[[], AbstractAsyncContextManager[EmbeddingModel]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    embeddings = parser.add_mutually_exclusive_group()
    embeddings.add_argument("--managed-embeddings", action="store_true")
    embeddings.add_argument("--local-embeddings", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        # Read only for a run, so a bad setting cannot stop the check above.
        settings = Settings()
        api_key = settings.model_api_key
        if arguments.managed_embeddings and api_key is None:
            sys.stderr.write(
                "set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus\n"
            )
            return 1
        source: ModelSource | None = None
        default = REPORT
        if arguments.managed_embeddings and api_key is not None:
            source, default = _openai_model(api_key), MANAGED_REPORT
        if arguments.local_embeddings:
            source, default = _local_model, LOCAL_REPORT
        runtime = create_database_runtime(settings)
        rendered = asyncio.run(_run(runtime, dataset, corpus, arguments.limit, source))
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
        return 1
    output: Path = arguments.output or default
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    source: ModelSource | None,
) -> str:
    observed_at = datetime.now(UTC)
    try:
        if source is None:
            report = await run_dataset(
                runtime, dataset, corpus, observed_at=observed_at, limit=limit
            )
            return render_report(report)
        async with source() as model:
            reports = await compare_branches(
                runtime,
                dataset,
                corpus,
                observed_at=observed_at,
                model=model,
                limit=limit,
            )
        return render_comparison(reports)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
    @asynccontextmanager
    async def source() -> AsyncGenerator[EmbeddingModel]:
        client = AsyncOpenAI(api_key=api_key.get_secret_value())
        try:
            yield OpenAIEmbeddingModel(client, OpenAIEmbeddingSettings())
        finally:
            await client.close()

    return source


@asynccontextmanager
async def _local_model() -> AsyncGenerator[EmbeddingModel]:
    # trust_env=False: a proxy set in the environment must never see the texts.
    async with httpx.AsyncClient(trust_env=False) as client:
        model = LocalEmbeddingModel(client, LocalEmbeddingSettings())
        # Before anything is written, so a stopped server leaves the database empty.
        await model.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
        yield model


if __name__ == "__main__":
    sys.exit(main())
```

Also document the setting. At the end of `.env.example`, add:

```text

# The local BGE-M3 server (ml/bge_m3_server); the default is shown.
# LOCAL_EMBEDDING_URL=http://127.0.0.1:8765
```

Check: with the server stopped and a migrated empty database in `KNOWLEDGE_SERVICE_DATABASE_URL`, `uv run python scripts/run_retrieval_dataset.py --local-embeddings` prints `the embedding model is not ready: EmbeddingUnavailable()` and exits with 1. Passing both model options prints `not allowed with argument`. `--verify-only` still prints `16 cases verified`.

### Step 8 — No coding in this step: run locally and compare with OpenAI

This step runs the real model and compares it with the managed one. It also breaks the provenance check on purpose, to see the tests catch it.

**Action 1:** start the server in one terminal and leave it running:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

**Action 2:** in a second terminal, run on a fresh database, twice, and compare:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings
cp artifacts/evaluations/bilingual-seed-local.md /tmp/first-local.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings > /dev/null
diff /tmp/first-local.md artifacts/evaluations/bilingual-seed-local.md && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Each run takes a few seconds once the model is loaded. No text leaves your machine.

**Action 3:** compare with OpenAI. The M05-T08 evidence file holds the managed report. In your evidence file, build one table with a row per slice and four columns: managed dense Recall@10 and MRR, local dense Recall@10 and MRR. Then look at three things:

- The cross-language slices, `en->uk` and `uk->en`, where the managed model was weakest by MRR.
- The identifier slice, and whether the local model also puts each code first.
- The fused run against the dense run, for each model: does adding word search help either one?

Write at least one **falsified assumption** in the shape "I assumed X. The report shows Y (case IDs and numbers). So Z is wrong."

**Action 4:** break the provenance check on purpose. In `src/knowledge_service/embeddings/local_adapter.py`, in `_check_provenance`, put a `#` in front of the line `or body.get("revision") != self._settings.revision`. Then run:

```bash
uv run pytest tests/test_local_embeddings.py -q
```

Expected: 2 failed, `test_vectors_from_other_weights_are_refused[other revision]` and `test_the_server_check_sends_no_text_and_refuses_other_weights`. A server running other weights would now be trusted. Remove the `#` and run the tests again until they pass. Stop the server with Ctrl+C when you are done.

### Step 9 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the architecture text the change made incomplete, and records the result.

**Action 1:** in `docs/architecture.md`, change the `embeddings` row of the "Code layout" table to:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `publish_document_embeddings`, the migration workflow |
```

Then, right after the paragraph that starts "Domain values, settings, the database runtime", add:

```markdown
Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings on Python 3.12, on
127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_local_embeddings.py tests/test_embedding_model_contract.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make ml-check
make docs-check
```

Expected: all pass. The reference run gave `make test-unit` 1238 passed, `make test-integration` 300 passed, and `make ml-check` 16 passed. One older hypothesis test from M05-T07 failed once in four full runs while the model server was busy in the background; it passes alone. If you see that, stop the server and run again.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why a separate Python 3.12 process; how provenance is pinned and checked; why the token count is an upper bound and how it was checked; why the server never truncates.
- **Verification:** the commands and counts, the side-by-side table from Step 8, the repeatability result, the falsified assumption, the deliberate break, and the known limits from "Before you begin". Paste the local report's summary table into the evidence file: `artifacts/` is ignored by Git.
- **Review:** an adversarial review of the prototype found four problems and three smaller ones, all fixed with regression tests that failed before the fix. NFKC alone undercounted Korean typed as separate letters by up to three times; the bound now takes the longer of NFKC and NFKD. A proxy set in the environment received the texts; the script's client now ignores the environment. A server answer was read whole, so a broken server could fill the memory; it is now capped at 8 MB. One huge text stalled the server; texts are now capped by length and counted off the request loop. Smaller: `1024.0` passed as `1024`, a broken URL crashed untyped inside a request, and `verify_server` ignored the configured deadline.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T09 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T10, and commit with `m05-t09: add local bge m3 embeddings`.

## Reflection questions

1. What does `LocalEmbeddingModel` hide from the code that calls it? List what a caller never has to think about, such as the URL, the HTTP status codes, or which revision the server runs.
2. Someone later "optimizes" the adapter by checking provenance only once, in `verify_server`, and not in every answer. What could then go wrong while a long run is embedding, and how would anyone notice?
3. The token count is an upper bound, about three times too high for plain text. What does that cost today, and what would break if someone replaced it with a guess that is sometimes too low?
4. Which of your predictions was wrong, and what did you believe about local or open models that made you predict it?
5. Sixteen made-up cases favour no model in particular. What kind of real question would you add to find out whether the local model is truly as good as the managed one?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `ml/bge_m3_server/` has its own `pyproject.toml`, `uv.lock`, server, and tests; `make ml-check` passes.
- [ ] The server pins the revision, checks file fingerprints, listens on 127.0.0.1, and refuses, never truncates, a text that is too long.
- [ ] `LocalEmbeddingModel` passes the shared contract suite.
- [ ] Vectors from another model, revision, or size raise `EmbeddingRequestRefused`; a stopped server raises `EmbeddingUnavailable`.
- [ ] No planted text appears in errors, linked errors, logs, or server answers.
- [ ] You ran `--local-embeddings` twice and compared it with the managed report in one table.
- [ ] The evidence file names at least one falsified assumption.
- [ ] You ran the deliberate break from Step 8, saw two tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, ML, docs.
- [ ] The architecture text, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
