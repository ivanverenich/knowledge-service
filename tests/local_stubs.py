"""A stand-in for the local BGE-M3 server, for deterministic tests.

`StubBgeM3Server` answers through `httpx.MockTransport`, so the real
`LocalEmbeddingModel` sends real HTTP requests that never leave the process.
"""

import asyncio
import json

import httpx

from fakes import word_weights
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
