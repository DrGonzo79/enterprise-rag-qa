"""OpenAI adapter — the second provider (SPEC-005 Interface).

Unlike the Anthropic adapter this one *does* send `temperature=0`: OpenAI still
accepts sampling parameters. The two providers are therefore not configured
identically, which is a real asymmetry under a provider-agnostic seam and is
recorded in KD-4 rather than hidden.

**No OpenAI rate rows ship in the pricing table**, so constructing this client
raises `UnknownModelError` until one is added, verified against OpenAI's own
pricing page. That is the designed behavior for an unpriced model (KD-10), not an
oversight — cost_usd is not-null and must never be guessed.
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from rag_qa.generation.clients.base import LLMResult, StopKind, TextChunk, Usage
from rag_qa.generation.pricing import resolve_rate

PROVIDER = "openai"


def _stop_kind(finish_reason: str | None) -> StopKind:
    if finish_reason == "length":
        return StopKind.MAX_TOKENS
    if finish_reason == "content_filter":
        return StopKind.REFUSAL
    return StopKind.NORMAL


class OpenAIClient:
    def __init__(self, model: str) -> None:
        from openai import AsyncOpenAI

        self.provider = PROVIDER
        self.model = model
        self.identity = f"{PROVIDER}:{model}"
        resolve_rate(self.identity)
        self._client: Any = AsyncOpenAI()

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        response: Any = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        choice = response.choices[0]
        stop = _stop_kind(choice.finish_reason)
        return LLMResult(
            text="" if stop is StopKind.REFUSAL else str(choice.message.content or ""),
            prompt_tokens=int(response.usage.prompt_tokens),
            completion_tokens=int(response.usage.completion_tokens),
            stop=stop,
        )

    @asynccontextmanager
    async def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AsyncGenerator[AsyncIterator[TextChunk | Usage]]:
        sdk_stream: Any = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0,
            stream=True,
            stream_options={"include_usage": True},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        async def events() -> AsyncIterator[TextChunk | Usage]:
            prompt_tokens = 0
            completion_tokens = 0
            stop = StopKind.NORMAL
            async for part in sdk_stream:
                if part.choices:
                    choice = part.choices[0]
                    content = getattr(choice.delta, "content", None)
                    if content:
                        yield TextChunk(str(content))
                    if choice.finish_reason:
                        stop = _stop_kind(choice.finish_reason)
                usage = getattr(part, "usage", None)
                if usage is not None:
                    prompt_tokens = int(usage.prompt_tokens)
                    completion_tokens = int(usage.completion_tokens)
            yield Usage(prompt_tokens, completion_tokens, stop)

        try:
            yield events()
        finally:
            await sdk_stream.close()
