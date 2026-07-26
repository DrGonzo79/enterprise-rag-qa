"""Request/response models — the source of truth for OpenAPI (SPEC-006 KD-15).

Every response is a declared model. A handler returning a bare dict produces an
OpenAPI entry documenting an untyped object, which is worse than no entry because
it looks like coverage.
"""

import uuid
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from rag_qa.api.conditions import Presentation, Reset
from rag_qa.generation.types import Answer, Citation
from rag_qa.ingest.pipeline import Manifest
from rag_qa.retrieval.types import RetrievalFilters, RetrievedChunk

# --- errors -------------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str
    request_id: str
    presentation: Presentation = Field(
        description=(
            "How a client should render this condition. `explanatory` means the service is "
            "deliberately not answering and the client should show the explanatory state "
            "(SPEC-006 Key decision 16) rather than an error page. Sent so a client need not "
            "keep its own copy of the taxonomy."
        )
    )
    reset: Reset = Field(
        description=(
            "When the condition clears. `window` means `Retry-After` is an accurate clock; "
            "`operator` means no countdown exists and one must not be rendered."
        )
    )


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- /query -------------------------------------------------------------------


class QueryFilters(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    source_uris: list[str] | None = None
    doc_types: list[str] | None = None

    def to_filters(self) -> RetrievalFilters:
        return RetrievalFilters(
            document_ids=tuple(self.document_ids) if self.document_ids else None,
            source_uris=tuple(self.source_uris) if self.source_uris else None,
            doc_types=tuple(self.doc_types) if self.doc_types else None,
        )


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, description="Natural-language question.")
    k: int = Field(default=8, ge=1, le=50, description="Chunks to retrieve.")
    filters: QueryFilters | None = None
    stream: bool = Field(
        default=False,
        description="true streams server-sent events; false returns application/json.",
    )


class CitationOut(BaseModel):
    marker: int
    chunk_id: uuid.UUID
    section_path: str = Field(
        description='Breadcrumb, e.g. "EU AI Act › CHAPTER III › Article 16".'
    )
    document_title: str
    source_uri: str
    doc_type: str

    @classmethod
    def build(cls, citation: Citation, doc_type: str) -> "CitationOut":
        return cls(
            marker=citation.marker,
            chunk_id=citation.chunk_id,
            section_path=citation.section_path,
            document_title=citation.document_title,
            source_uri=citation.source_uri,
            doc_type=doc_type,
        )


class UsageOut(BaseModel):
    generator_identity: str
    prompt_tokens: int
    completion_tokens: int
    # A string, not a float: cost_usd is numeric(10,6) and JSON floats are
    # binary, so a number would be a lossy round-trip on a money column.
    cost_usd: str
    latency_ms: int
    prompt_version: str

    @classmethod
    def build(cls, answer: Answer) -> "UsageOut":
        return cls(
            generator_identity=answer.generator_identity,
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            cost_usd=str(answer.cost_usd),
            latency_ms=answer.latency_ms,
            prompt_version=answer.prompt_version,
        )


class QueryResponse(BaseModel):
    request_id: str
    verdict: Literal["answered", "insufficient_evidence", "truncated", "provider_refused", "error"]
    answer: str
    citations: list[CitationOut]
    dropped_markers: list[int]
    usage: UsageOut

    @classmethod
    def build(
        cls, answer: Answer, chunks: Sequence[RetrievedChunk], request_id: str
    ) -> "QueryResponse":
        doc_types = {chunk.chunk_id: chunk.doc_type for chunk in chunks}
        return cls(
            request_id=request_id,
            verdict=str(answer.verdict),  # type: ignore[arg-type]
            answer=answer.text,
            citations=[
                CitationOut.build(citation, doc_types.get(citation.chunk_id, ""))
                for citation in answer.citations
            ],
            dropped_markers=list(answer.dropped_markers),
            usage=UsageOut.build(answer),
        )


# --- /ingest ------------------------------------------------------------------


class IngestRequest(BaseModel):
    paths: list[str] = Field(
        default_factory=lambda: ["corpus"],
        description="Paths relative to the server-side corpus directory.",
    )
    dry_run: bool = Field(
        default=True,
        description="Default true: the destructive, billable call is the one you ask for.",
    )


class IngestDocument(BaseModel):
    document: str
    verdict: str
    chunks: int
    tokens: int
    estimated_embedding_usd: float


class IngestResponse(BaseModel):
    request_id: str
    dry_run: bool
    documents: list[IngestDocument]
    total_chunks: int
    estimated_embedding_usd: float

    @classmethod
    def build(cls, manifest: Manifest, *, dry_run: bool, request_id: str) -> "IngestResponse":
        documents = [
            IngestDocument(
                document=report.document,
                verdict=report.verdict,
                chunks=report.chunks,
                tokens=report.tokens,
                estimated_embedding_usd=report.estimated_embedding_usd,
            )
            for report in manifest.documents
        ]
        return cls(
            request_id=request_id,
            dry_run=dry_run,
            documents=documents,
            total_chunks=sum(d.chunks for d in documents),
            estimated_embedding_usd=round(sum(d.estimated_embedding_usd for d in documents), 6),
        )


# --- health -------------------------------------------------------------------


class HealthzResponse(BaseModel):
    """SPEC-001 liveness. Deliberately dependency-free."""

    status: Literal["ok"]


class CheckResult(BaseModel):
    ok: bool
    detail: str | None = None
    latency_ms: int | None = None
    revision: str | None = None
    chunks: int | None = None
    embedder_identity: str | None = None
    identity: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, CheckResult]
