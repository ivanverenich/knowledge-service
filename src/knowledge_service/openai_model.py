"""OpenAI implementation of the provider-neutral ChatModel interface."""

import asyncio
from uuid import UUID

import openai
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings

from knowledge_service.contracts import Answer, Question, Usage
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelTimeout,
)


class OpenAISettings(BaseSettings):
    """Settings required by the OpenAI adapter."""

    model_name: str = "gpt-4.1-mini"
    model_timeout_seconds: float = 30.0
    model_api_key: SecretStr | None = None


class OpenAIChatModel:
    """Translate ChatModel calls to the OpenAI Responses API."""

    def __init__(self, client: AsyncOpenAI, settings: OpenAISettings) -> None:
        self._client = client
        self._settings = settings

    async def answer(self, question: Question, *, request_id: UUID) -> Answer:
        try:
            response = await self._client.responses.create(
                model=self._settings.model_name,
                input=question.text,
                timeout=self._settings.model_timeout_seconds,
                extra_headers={"X-Request-ID": str(request_id)},
            )
        except openai.APITimeoutError as error:
            raise ChatModelTimeout from error
        except openai.RateLimitError as error:
            raise ChatModelRateLimited from error
        except asyncio.CancelledError:
            raise

        if response.usage is None:
            raise ChatModelMalformedResponse("Missing usage data")

        return Answer(
            text=response.output_text,
            request_id=request_id,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            ),
        )
