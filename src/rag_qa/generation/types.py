"""Answer, Citation, Verdict and stream events (SPEC-005 Interface)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Verdict(StrEnum):
    ANSWERED = "answered"
    # The model declined for lack of supporting evidence. This is a SUCCESS —
    # refusal is a scored capability (charter), not a failure mode.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    TRUNCATED = "truncated"
    # The provider's safety classifier declined. Deliberately distinct from
    # INSUFFICIENT_EVIDENCE: conflating them would blend "correctly declined for
    # lack of evidence" with "provider wouldn't answer" (SPEC-005 KD-5).
    PROVIDER_REFUSED = "provider_refused"
    ERROR = "error"


@dataclass(frozen=True)
class Citation:
    marker: int  # the n in [n], 1-based, as the model wrote it
    chunk_id: uuid.UUID
    section_path: str
    document_title: str
    source_uri: str


@dataclass(frozen=True)
class Answer:
    text: str  # verdict line stripped; [n] markers retained in place
    verdict: Verdict
    citations: tuple[Citation, ...]  # deduplicated, first-appearance order
    generator_identity: str  # from the client, never a constant (KD-1)
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    dropped_markers: tuple[int, ...]  # out-of-range markers stripped (KD-9)


# --- stream events ------------------------------------------------------------


@dataclass(frozen=True)
class VerdictEvent:
    """Always first: lets a client render a refusal without waiting for prose."""

    verdict: Verdict


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class CitationEvent:
    citation: Citation


@dataclass(frozen=True)
class AnswerComplete:
    """Always last; the only event carrying usage, because that is when the
    provider reports it."""

    answer: Answer


AnswerEvent = VerdictEvent | TextDelta | CitationEvent | AnswerComplete


class UnknownModelError(RuntimeError):
    """Model identity absent from the pricing table (SPEC-005 KD-10).

    Raised at client construction, not at request time: recording cost 0 for an
    unpriced model would write a falsehood into the column whose only purpose is
    cost tracking.
    """
