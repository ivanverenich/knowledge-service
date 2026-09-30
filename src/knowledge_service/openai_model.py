"""OpenAI implementation of the provider-neutral ChatModel interface."""

import asyncio
import time
from uuid import UUID

import openai
from openai import AsyncOpenAI
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings

from knowledge_service.contracts import (
    Answer,
    GeneratedAnswer,
    Question,
    Usage,
)
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRefused,
    ChatModelStructuredOutputUnsupported,
    ChatModelTimeout,
)


class OpenAISettings(BaseSettings):
    """Settings required by the OpenAI adapter."""

    model_name: str = "gpt-4.1-mini"
    model_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    model_api_key: SecretStr | None = None
    model_price_id: str | None = None


class OpenAIChatModel:
    """Translate ChatModel calls to the OpenAI Responses API."""

    def __init__(self, client: AsyncOpenAI, settings: OpenAISettings) -> None:
        self._client = client
        self._settings = settings

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Answer:
        request_timeout_seconds = min(
            timeout_seconds,
            self._settings.model_timeout_seconds,
        )
        started_at = time.perf_counter()
        try:
            response = await self._client.responses.parse(
                model=self._settings.model_name,
                input=question.text,
                text_format=GeneratedAnswer,
                timeout=request_timeout_seconds,
                extra_headers={"X-Request-ID": str(request_id)},
            )
        except openai.APITimeoutError as error:
            raise ChatModelTimeout from error
        except openai.RateLimitError as error:
            raise ChatModelRateLimited from error
        except ValidationError as error:
            raise ChatModelMalformedResponse("Invalid structured answer") from error
        except openai.BadRequestError as error:
            if error.param == "text.format" and error.code == "unsupported_value":
                raise ChatModelStructuredOutputUnsupported from error
            raise
        except asyncio.CancelledError:
            raise

        if any(
            content.type == "refusal"
            for item in response.output
            if item.type == "message"
            for content in item.content
        ):
            raise ChatModelRefused

        if response.usage is None:
            raise ChatModelMalformedResponse("Missing usage data")

        generated = response.output_parsed
        if generated is None:
            raise ChatModelMalformedResponse("Missing structured answer")

        latency_ms = round((time.perf_counter() - started_at) * 1_000)

        return Answer(
            text=generated.text,
            request_id=request_id,
            usage=Usage(
                provider="openai",
                model=self._settings.model_name,
                price_id=self._settings.model_price_id,
                latency_ms=latency_ms,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            ),
        )
