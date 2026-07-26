"""Anthropic adapter (SPEC-005 Interface).

Request-shape notes, each load-bearing:

- **No `temperature`/`top_p`/`top_k`.** Sampling parameters were removed from the
  Claude API; sending one returns HTTP 400 on current models (KD-4).
- **`thinking` is omitted**, which runs adaptive thinking on Sonnet 5 and Opus 5.
  Disabling it is unsafe here for two separate reasons: leaked `<thinking>` tags
  would corrupt the verdict/marker parser (KD-6), and a first-line verdict with no
  prior reasoning creates confabulation pressure (KD-7).
- **`stop_reason` is checked before `content` is read** — a classifier refusal
  returns HTTP 200 with an empty content list, so an unguarded `content[0]`
  raises (KD-5).
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from rag_qa.generation.clients.base import LLMResult, StopKind, TextChunk, Usage
from rag_qa.generation.pricing import resolve_rate

DEFAULT_MODEL = "claude-sonnet-5"
PROVIDER = "anthropic"


def _stop_kind(stop_reason: str | None) -> StopKind:
    if stop_reason == "refusal":
        return StopKind.REFUSAL
    if stop_reason == "max_tokens":
        return StopKind.MAX_TOKENS
    return StopKind.NORMAL


class AnthropicClient:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        from anthropic import AsyncAnthropic

        self.provider = PROVIDER
        self.model = model
        self.identity = f"{PROVIDER}:{model}"
        # Fail here rather than at request time: an unpriced model must not reach
        # a query_log row (KD-10).
        resolve_rate(self.identity)
        self._client: Any = AsyncAnthropic()

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        response: Any = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        stop = _stop_kind(response.stop_reason)
        if stop is StopKind.REFUSAL:
            # Content is empty or partial; do not read it.
            return LLMResult(
                text="",
                prompt_tokens=int(response.usage.input_tokens),
                completion_tokens=int(response.usage.output_tokens),
                stop=stop,
            )
        text = "".join(str(block.text) for block in response.content if block.type == "text")
        return LLMResult(
            text=text,
            prompt_tokens=int(response.usage.input_tokens),
            completion_tokens=int(response.usage.output_tokens),
            stop=stop,
        )

    @asynccontextmanager
    async def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AsyncGenerator[AsyncIterator[TextChunk | Usage]]:
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as sdk_stream:

            async def events() -> AsyncIterator[TextChunk | Usage]:
                prompt_tokens = 0
                completion_tokens = 0
                stop = StopKind.NORMAL
                async for event in sdk_stream:
                    kind = getattr(event, "type", "")
                    if kind == "message_start":
                        prompt_tokens = int(event.message.usage.input_tokens)
                    elif kind == "content_block_delta":
                        # thinking_delta is dropped: only visible text reaches the
                        # parser, so thinking never corrupts marker parsing.
                        if getattr(event.delta, "type", "") == "text_delta":
                            yield TextChunk(str(event.delta.text))
                    elif kind == "message_delta":
                        if getattr(event, "usage", None) is not None:
                            completion_tokens = int(event.usage.output_tokens)
                        stop = _stop_kind(getattr(event.delta, "stop_reason", None))
                yield Usage(prompt_tokens, completion_tokens, stop)

            yield events()
