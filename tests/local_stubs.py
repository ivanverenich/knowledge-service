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
