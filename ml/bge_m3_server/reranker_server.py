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
