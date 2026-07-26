"""Answer generation (SPEC-005): context-only answering with inline citations,
an explicit refusal verdict, streaming, and per-request cost logging."""

from rag_qa.generation.citations import AnswerParser, parse_answer
from rag_qa.generation.clients.base import LLMClient, LLMResult, StopKind
from rag_qa.generation.pricing import compute_cost, resolve_rate
from rag_qa.generation.prompt import PROMPT_VERSION, SYSTEM_PROMPT, render_context
from rag_qa.generation.service import Generator
from rag_qa.generation.types import (
    Answer,
    AnswerComplete,
    AnswerEvent,
    Citation,
    CitationEvent,
    TextDelta,
    UnknownModelError,
    Verdict,
    VerdictEvent,
)

__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "Answer",
    "AnswerComplete",
    "AnswerEvent",
    "AnswerParser",
    "Citation",
    "CitationEvent",
    "Generator",
    "LLMClient",
    "LLMResult",
    "StopKind",
    "TextDelta",
    "UnknownModelError",
    "Verdict",
    "VerdictEvent",
    "compute_cost",
    "parse_answer",
    "render_context",
    "resolve_rate",
]
