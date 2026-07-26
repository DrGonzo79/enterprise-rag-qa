"""Provider seam (SPEC-005 Interface).

Everything provider-shaped stays behind this protocol: Anthropic's
input_tokens/output_tokens and OpenAI's prompt_tokens/completion_tokens both
normalize to the latter pair, and both event streams normalize to
TextChunk/Usage. Callers never import a provider SDK type.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class StopKind(StrEnum):
    NORMAL = "normal"
    MAX_TOKENS = "max_tokens"
    # The provider's safety classifier declined. Distinct from the model
    # deciding the evidence is insufficient (SPEC-005 KD-5).
    REFUSAL = "refusal"


@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    stop: StopKind


@dataclass(frozen=True)
class TextChunk:
    text: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    stop: StopKind


LLMStreamEvent = TextChunk | Usage


class LLMClient(Protocol):
    # "provider:model" — same invariant as EmbeddingClient: the value comes from
    # the client, never a constant, so a model swap is visible in the data.
    identity: str
    provider: str
    model: str

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult: ...

    def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AbstractAsyncContextManager[AsyncIterator[LLMStreamEvent]]: ...
