"""POST /ask — retrieve, answer, log (SPEC-005 Interface)."""

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from rag_qa.generation.service import Generator
from rag_qa.generation.types import (
    CitationEvent,
    TextDelta,
    VerdictEvent,
)
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import (
    EmbedderMismatchError,
    EmptyCorpusError,
    RetrievalFilters,
)

router = APIRouter()


class AskFilters(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    source_uris: list[str] | None = None
    doc_types: list[str] | None = None

    def to_filters(self) -> RetrievalFilters:
        return RetrievalFilters(
            document_ids=tuple(self.document_ids) if self.document_ids else None,
            source_uris=tuple(self.source_uris) if self.source_uris else None,
            doc_types=tuple(self.doc_types) if self.doc_types else None,
        )


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)
    filters: AskFilters | None = None
    stream: bool = False


def build_router(retriever: Retriever, generator: Generator) -> APIRouter:
    ask_router = APIRouter()

    @ask_router.post("/ask")
    async def ask(request: AskRequest):  # type: ignore[no-untyped-def]
        if not request.question.strip():
            raise HTTPException(status_code=422, detail="question must not be blank")
        filters = request.filters.to_filters() if request.filters else None
        try:
            chunks = await retriever.retrieve(request.question, request.k, filters)
        except (EmbedderMismatchError, EmptyCorpusError) as exc:
            # Operational fault, not a bad request: the corpus and the query
            # embedder disagree, or there is no corpus at all.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if not request.stream:
            answer = await generator.answer(request.question, chunks)
            return {
                "answer": answer.text,
                "verdict": str(answer.verdict),
                "citations": [
                    {
                        "marker": citation.marker,
                        "chunk_id": str(citation.chunk_id),
                        "section_path": citation.section_path,
                        "document_title": citation.document_title,
                        "source_uri": citation.source_uri,
                    }
                    for citation in answer.citations
                ],
                "usage": {
                    "prompt_tokens": answer.prompt_tokens,
                    "completion_tokens": answer.completion_tokens,
                    "cost_usd": str(answer.cost_usd),
                    "latency_ms": answer.latency_ms,
                    "generator_identity": answer.generator_identity,
                    "prompt_version": answer.prompt_version,
                },
            }

        async def event_stream() -> AsyncIterator[str]:
            async for event in generator.stream_answer(request.question, chunks):
                if isinstance(event, VerdictEvent):
                    payload = {"type": "verdict", "verdict": str(event.verdict)}
                elif isinstance(event, TextDelta):
                    payload = {"type": "text", "text": event.text}
                elif isinstance(event, CitationEvent):
                    payload = {
                        "type": "citation",
                        "marker": event.citation.marker,
                        "chunk_id": str(event.citation.chunk_id),
                        "section_path": event.citation.section_path,
                    }
                else:
                    payload = {
                        "type": "complete",
                        "verdict": str(event.answer.verdict),
                        "prompt_tokens": event.answer.prompt_tokens,
                        "completion_tokens": event.answer.completion_tokens,
                        "cost_usd": str(event.answer.cost_usd),
                        "latency_ms": event.answer.latency_ms,
                    }
                yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return ask_router
