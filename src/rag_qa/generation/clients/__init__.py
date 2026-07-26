"""Provider adapters. Import the concrete clients directly so a missing
provider SDK never breaks importing the generation package."""

from rag_qa.generation.clients.base import (
    LLMClient,
    LLMResult,
    LLMStreamEvent,
    StopKind,
    TextChunk,
    Usage,
)

__all__ = [
    "LLMClient",
    "LLMResult",
    "LLMStreamEvent",
    "StopKind",
    "TextChunk",
    "Usage",
]
