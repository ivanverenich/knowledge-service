"""A stand-in for AsyncOpenAI's embeddings endpoint, for deterministic tests."""

import asyncio
import hashlib
import math
from typing import Any, cast

import httpx2
import openai
from openai import AsyncOpenAI
from openai.types import CreateEmbeddingResponse

from knowledge_service.openai_embeddings import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)

URL = "https://api.openai.test/v1/embeddings"


def vector_for(text: str, dimensions: int) -> list[float]:
    """A stable unit vector for `text`, as a real model would give."""
    raw = [b / 127.5 - 1.0 for b in hashlib.shake_256(text.encode()).digest(dimensions)]
    norm = math.sqrt(sum(value * value for value in raw))
    return [value / norm for value in raw]


def response_for(
    texts: list[str], dimensions: int, *, reverse: bool = True
) -> CreateEmbeddingResponse:
    """A response whose items come back in reverse order, each with its index."""
    data = [
        {
            "object": "embedding",
            "index": index,
            "embedding": vector_for(text, dimensions),
        }
        for index, text in enumerate(texts)
    ]
    return CreateEmbeddingResponse.model_validate(
        {
            "object": "list",
            "model": "text-embedding-3-small",
            "data": list(reversed(data)) if reverse else data,
            "usage": {"prompt_tokens": len(texts) * 3, "total_tokens": len(texts) * 3},
        }
    )


def status_error(status: int, *, body_text: str = "") -> openai.APIStatusError:
    """An SDK error whose request, like the real one, holds the input texts."""
    request = httpx2.Request("POST", URL, json={"input": [body_text]})
    response = httpx2.Response(status, request=request)
    error_type = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        409: openai.ConflictError,
        422: openai.UnprocessableEntityError,
        429: openai.RateLimitError,
    }.get(
        status, openai.InternalServerError if status >= 500 else openai.APIStatusError
    )
    return error_type(f"status {status}", response=response, body=None)


class StubEmbeddings:
    """Replace only `AsyncOpenAI.embeddings`; record every request."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        response: object = None,
        dimensions: int | None = None,
        delay: float | None = None,
        block: bool = False,
    ) -> None:
        self._error = error
        self._response = response
        self._dimensions = dimensions
        self._delay = delay
        self._block = block
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._block:
            await asyncio.Event().wait()
        if self._delay is not None:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return response_for(kwargs["input"], self._dimensions or kwargs["dimensions"])


class UnconfiguredEmbeddings:
    """The endpoint of the client as passed in, with the SDK's retries on."""

    async def create(self, **kwargs: Any) -> CreateEmbeddingResponse:
        raise AssertionError("the adapter used the client without its options")


class StubClient:
    """Enough of AsyncOpenAI for the adapter: `with_options` and `embeddings`.

    Like the real client, `with_options` returns a configured copy. Only the
    copy reaches the stubbed endpoint, so an adapter that ignores the copy
    fails loudly.
    """

    def __init__(self, embeddings: StubEmbeddings) -> None:
        self._configured = embeddings
        self.embeddings: StubEmbeddings | UnconfiguredEmbeddings = (
            UnconfiguredEmbeddings()
        )
        self.options: dict[str, Any] = {}
        self.copies: list[StubClient] = []

    def with_options(self, **options: Any) -> StubClient:
        copy = StubClient(self._configured)
        copy.embeddings = self._configured
        copy.options = {**self.options, **options}
        self.copies.append(copy)
        return copy


def openai_embedding_model(
    embeddings: StubEmbeddings,
    settings: OpenAIEmbeddingSettings | None = None,
) -> tuple[OpenAIEmbeddingModel, StubClient]:
    """Return the adapter and the configured client copy it must use."""
    client = StubClient(embeddings)
    model = OpenAIEmbeddingModel(
        cast(AsyncOpenAI, client), settings or OpenAIEmbeddingSettings()
    )
    (configured,) = client.copies
    return model, configured
