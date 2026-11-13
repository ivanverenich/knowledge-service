"""OpenAI implementation of the AnswerModel port, and the answer settings.

One call to `generate` is one request to the Responses API. The adapter
adds what the port cannot know:

- **A pinned model.** The model is a dated snapshot, so a provider update
  cannot change the answers silently.
- **The prompt as rendered.** The fixed instructions, the input document,
  and the response schema come from `render_prompt` (see
  `answering.prompt`) unchanged, so what was counted is what is sent.
- **A constrained answer.** Structured Outputs force the reply into the
  prompt's schema, whose citations allow only this request's Evidence
  IDs. That narrows what the model can write; `checked_answer` still
  decides what counts.
- **Nothing kept.** `store=False`, so the provider does not keep the
  response for later retrieval.
- **A deadline without hidden retries.** The SDK's own retries are off, so
  a timeout means what it says.
- **Cost capture.** Every reply returns the billed input and output
  tokens, also a refusal or a reply cut off at its token limit.
- **Safe logging.** One log record per call with counts, tokens, timing,
  and the outcome; never text. Errors are raised outside the SDK's `except`
  block, because its error holds the request, and the request holds the
  Question and the Evidence.

`AnswerSettings`, read from `ANSWER_*`, also gives the `ContextBudget`:
`context_tokens` is a budget chosen for cost and speed, well below the
model's own window, and `max_output_tokens` is the Answer's reserve.
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Self, cast
from uuid import UUID

import openai
from openai import AsyncOpenAI, omit
from openai.types.shared_params import Reasoning, ReasoningEffort
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.answering.context_budget import ContextBudget
from knowledge_service.answering.generation import (
    InvalidAnswerRequest,
    ModelIdentity,
    ModelReply,
    positive_seconds,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
    ChatModelUnavailable,
)
from knowledge_service.pricing import ModelPrice

logger = logging.getLogger(__name__)

PROVIDER = "openai"


class AnswerSettings(BaseSettings):
    """Settings for answering, read from `ANSWER_*`.

    The prices are the provider's list prices for the default model on
    2026-11-06, in US dollars per million tokens; set both when the model
    changes.
    """

    model_config = SettingsConfigDict(env_prefix="ANSWER_")

    model: str = Field(default="gpt-5.4-mini-2026-03-17", min_length=1)
    # None sends no reasoning setting, for a model that has none.
    reasoning_effort: ReasoningEffort = "none"
    timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1_000, gt=0, le=8_192)
    context_tokens: int = Field(default=32_000, gt=0)
    history_tokens: int = Field(default=4_000, ge=0)
    input_usd_per_million_tokens: Decimal = Field(default=Decimal("0.75"), ge=0)
    output_usd_per_million_tokens: Decimal = Field(default=Decimal("4.50"), ge=0)

    @model_validator(mode="after")
    def _fits(self) -> Self:
        # Raises InvalidContextBudget, a ValueError, when the reserves fill it.
        _ = self.budget
        return self

    @property
    def budget(self) -> ContextBudget:
        """The context budget: the window, the Answer's and History's room."""
        return ContextBudget(
            context_tokens=self.context_tokens,
            answer_tokens=self.max_output_tokens,
            history_tokens=self.history_tokens,
        )


class OpenAIAnswerModel:
    """Translate AnswerModel calls to the OpenAI Responses API."""

    def __init__(self, client: AsyncOpenAI, settings: AnswerSettings) -> None:
        # Retries are the caller's job; hidden ones would stretch the deadline.
        self._client = client.with_options(max_retries=0)
        self._settings = settings
        self._identity = ModelIdentity(provider=PROVIDER, model=settings.model)

    @property
    def identity(self) -> ModelIdentity:
        return self._identity

    @property
    def price(self) -> ModelPrice:
        """The configured price, under the model's name as its price ID."""
        return ModelPrice(
            price_id=self._settings.model,
            input_usd_per_million_tokens=self._settings.input_usd_per_million_tokens,
            output_usd_per_million_tokens=self._settings.output_usd_per_million_tokens,
        )

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        if type(prompt) is not EvidencePrompt:
            raise InvalidAnswerRequest("prompt must be an EvidencePrompt")
        deadline = self._deadline(timeout_seconds)
        effort = self._settings.reasoning_effort
        started_at = time.perf_counter()
        response: object = None
        failure: ChatModelError | None = None
        try:
            async with asyncio.timeout(deadline):
                response = await self._client.responses.create(
                    model=self._settings.model,
                    instructions=prompt.instructions,
                    input=prompt.input,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "answer",
                            "strict": True,
                            "schema": prompt.response_schema(),
                        }
                    },
                    max_output_tokens=self._settings.max_output_tokens,
                    store=False,
                    timeout=deadline,
                    extra_headers={"X-Request-ID": str(request_id)},
                    reasoning=omit if effort is None else Reasoning(effort=effort),
                )
        except (TimeoutError, openai.APIError) as error:
            failure = _translated(error)
        except ValueError:
            # A successful status with a body that is not JSON.
            failure = ChatModelMalformedResponse("the provider response was not JSON")
        except asyncio.CancelledError:
            # The caller's own deadline usually ends a slow call: still record it.
            _log(request_id, self._identity, _since(started_at), "cancelled")
            raise
        latency_ms = _since(started_at)
        try:
            if failure is not None:
                # Raised outside the SDK's except block: its error holds the request.
                raise failure
            usage = self._usage(response, latency_ms)
        except ChatModelError as error:
            _log(request_id, self._identity, latency_ms, type(error).__name__)
            raise
        text, refused = _reply(response)
        outcome = (
            "refused" if refused else "succeeded" if text is not None else "unreadable"
        )
        _log(request_id, self._identity, latency_ms, outcome, usage)
        return ModelReply(text=text, refused=refused, usage=usage)

    def _deadline(self, timeout_seconds: float) -> float:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        return min(float(timeout_seconds), self._settings.timeout_seconds)

    def _usage(self, response: object, latency_ms: int) -> Usage:
        """Read the billed tokens without trusting the response's shape."""
        usage = getattr(response, "usage", None)
        tokens = [
            getattr(usage, name, None) for name in ("input_tokens", "output_tokens")
        ]
        if any(type(value) is not int or value < 0 for value in tokens):
            raise ChatModelMalformedResponse("the response has no token usage")
        input_tokens, output_tokens = cast(list[int], tokens)
        return Usage(
            provider=PROVIDER,
            model=self._settings.model,
            price_id=self._settings.model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )


def _reply(response: object) -> tuple[str | None, bool]:
    """The reply's text and whether the model refused.

    A refusal gives `(None, True)`. An answer cut off at its token limit,
    or an output of the wrong shape, gives `(None, False)`; the caller
    still bills it. The SDK does not check a successful body, so every
    part is checked here before it is read.
    """
    output = getattr(response, "output", None)
    if type(output) is not list:
        return None, False
    parts: list[str] = []
    for item in cast(list[object], output):
        content = getattr(item, "content", None)
        if content is None:
            continue
        if type(content) is not list:
            return None, False
        for part in cast(list[object], content):
            kind = getattr(part, "type", None)
            if kind == "refusal":
                return None, True
            text = getattr(part, "text", None)
            if kind == "output_text" and type(text) is not str:
                return None, False
            if kind == "output_text":
                parts.append(cast(str, text))
    if getattr(response, "status", None) != "completed" or not parts:
        return None, False
    return "".join(parts), False


def _translated(error: TimeoutError | openai.APIError) -> ChatModelError:
    """Map one SDK failure to the port error that says whether to retry."""
    match error:
        case (
            TimeoutError()
            | openai.APITimeoutError()
            | openai.APIStatusError(status_code=408)
        ):
            return ChatModelTimeout()
        case openai.RateLimitError():
            return ChatModelRateLimited()
        case (
            openai.APIConnectionError()
            | openai.InternalServerError()
            | openai.ConflictError()
        ):
            return ChatModelUnavailable()
        case openai.APIStatusError():
            return ChatModelRequestRefused(
                f"the provider refused the request with status {error.status_code}"
            )
        case _:
            return ChatModelMalformedResponse("the provider response was not readable")


def _since(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1_000)


def _log(
    request_id: UUID,
    identity: ModelIdentity,
    latency_ms: int,
    outcome: str,
    usage: Usage | None = None,
) -> None:
    """Record one call: identifiers, tokens, timing, outcome. No text.

    `outcome` is "succeeded", "refused", "unreadable", "cancelled", or the
    name of the port error raised; the adapter chooses it, never the
    provider.
    """
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "latency_ms": latency_ms,
        "outcome": outcome,
    }
    if usage is not None:
        fields["input_tokens"] = usage.input_tokens
        fields["output_tokens"] = usage.output_tokens
    level = logging.INFO if outcome == "succeeded" else logging.WARNING
    logger.log(level, "answer model call finished", extra={"answer_model": fields})
