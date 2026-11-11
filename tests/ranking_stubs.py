"""A stand-in for AsyncOpenAI's Responses endpoint, for deterministic tests."""

import asyncio
import json
from typing import Any

from openai.types.responses import Response

from knowledge_service.retrieval.openai_ranking import (
    OpenAIRankingModel,
    OpenAIRankingSettings,
)


def response_for(
    text: str | None,
    *,
    status: str = "completed",
    input_tokens: int = 120,
    output_tokens: int = 12,
) -> Response:
    """A Responses API answer holding `text`, or a refusal when it is None."""
    content: dict[str, object] = (
        {"type": "refusal", "refusal": "I cannot help with that."}
        if text is None
        else {"type": "output_text", "text": text, "annotations": []}
    )
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "model": "gpt-test",
            "status": status,
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [content],
                }
            ],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": input_tokens + output_tokens,
            },
        }
    )


def ranked_ids(kwargs: dict[str, Any]) -> list[str]:
    """The IDs the request sent, in the order sent."""
    payload = json.loads(kwargs["input"])
    return [passage["id"] for passage in payload["passages"]]


class StubResponses:
    """Replace only `AsyncOpenAI.responses`; record every request.

    By default it answers with the IDs in reverse order. `text` answers
    with that text instead, `response` with that object, and `error`
    raises. `block` never answers.
    """

    def __init__(
        self,
        *,
        text: str | None = "",
        response: object = None,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._text = text
        self._response = response
        self._error = error
        self._block = block
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        if self._text == "":
            ids = list(reversed(ranked_ids(kwargs)))
            return response_for(json.dumps({"ranking": ids}))
        return response_for(self._text)


class UnconfiguredResponses:
    """The endpoint of the client as passed in, with the SDK's retries on."""

    async def create(self, **kwargs: Any) -> Response:
        raise AssertionError("the adapter used the client without its options")


class StubClient:
    """Enough of AsyncOpenAI for the adapter: `with_options` and `responses`.

    Only the configured copy reaches the stub, so an adapter that ignores
    `with_options` fails loudly.
    """

    def __init__(self, responses: StubResponses) -> None:
        self._configured = responses
        self.responses: StubResponses | UnconfiguredResponses = UnconfiguredResponses()
        # The options of every configured copy asked for, in order.
        self.option_calls: list[dict[str, Any]] = []

    def with_options(self, **options: Any) -> StubClient:
        self.option_calls.append(options)
        copy = StubClient(self._configured)
        copy.responses = self._configured
        return copy


def openai_ranker(
    responses: StubResponses, **settings: Any
) -> tuple[OpenAIRankingModel, StubClient]:
    """An adapter over the stub, and the client it was given."""
    client = StubClient(responses)
    ranker = OpenAIRankingModel(
        client,  # pyright: ignore[reportArgumentType]
        OpenAIRankingSettings(**settings),
    )
    return ranker, client
