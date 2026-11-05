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
- **Sparse term weights** from the same pass through the model: for each
  input, the vocabulary pieces it contains, each with one positive weight
  (BGE-M3's lexical matching output). Special tokens are left out.
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
from collections.abc import Callable, Sequence
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
# Token IDs in sparse weights are below this: the size of the vocabulary.
VOCABULARY_SIZE = 250_002
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
    # The layer that turns each token's hidden state into its sparse weight.
    "sparse_linear.pt": "45c93804d2142b8f6d7ec6914ae23a1eee9c6a1d27d83d908a20d2afb3595ad9",  # noqa: E501
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

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        """Return, per text in order, a weight for each token ID it contains."""
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
