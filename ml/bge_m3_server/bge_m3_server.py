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
