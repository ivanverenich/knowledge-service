"""A deterministic RewriteModel for tests: no network, fixed answers."""

import asyncio
import time
from collections.abc import Callable, Sequence
from uuid import UUID

from knowledge_service.contracts import Usage
from knowledge_service.retrieval.query_rewriting import (
    RewriteAnswer,
    RewriterIdentity,
    RewriterTimeout,
)
from knowledge_service.retrieval.rewrite_classes import RewritePlan

type Answer = Callable[[str, RewritePlan, tuple[str, ...]], object]


def joined(question: str, plan: RewritePlan, history: tuple[str, ...]) -> str:
    """The last earlier Question, then the Question: a follow-up made whole."""
    return " ".join([*history[-1:], question])


class FakeRewriteModel:
    """Rewrite by `answer`, by default `joined`; record every call.

    It bills one input token per word sent and one output token per word
    written. `error` is raised instead of an answer. `delay_seconds` waits
    that long within the call's own timeout; `block_seconds` blocks the
    event loop that long, ignoring it.
    """

    def __init__(
        self,
        *,
        answer: Answer = joined,
        error: BaseException | None = None,
        delay_seconds: float = 0.0,
        block_seconds: float = 0.0,
        model: str = "fake-rewriter",
    ) -> None:
        self._identity = RewriterIdentity(provider="fake", model=model)
        self._answer = answer
        self._error = error
        self._delay_seconds = delay_seconds
        self._block_seconds = block_seconds
        self.calls: list[tuple[str, RewritePlan, tuple[str, ...]]] = []

    @property
    def identity(self) -> RewriterIdentity:
        return self._identity

    async def rewrite(
        self,
        question: str,
        plan: RewritePlan,
        *,
        history: Sequence[str],
        request_id: UUID,
        timeout_seconds: float,
    ) -> RewriteAnswer:
        earlier = tuple(history)
        self.calls.append((question, plan, earlier))
        if self._delay_seconds:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await asyncio.sleep(self._delay_seconds)
            except TimeoutError:
                raise RewriterTimeout() from None
        if self._block_seconds:
            time.sleep(self._block_seconds)  # noqa: ASYNC251 - blocks on purpose
        if self._error is not None:
            raise self._error
        text = self._answer(question, plan, earlier)
        sent = len(question.split()) + sum(len(q.split()) for q in earlier)
        written = len(text.split()) if type(text) is str else 0
        return RewriteAnswer(
            text=text,
            usage=Usage(
                provider="fake",
                model=self._identity.model,
                price_id=self._identity.model,
                latency_ms=0,
                input_tokens=sent,
                output_tokens=written,
                total_tokens=sent + written,
            ),
        )
