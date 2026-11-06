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
