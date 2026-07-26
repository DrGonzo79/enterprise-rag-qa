"""POST /ask tests from SPEC-005 AC-11. No database, no provider."""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rag_qa.generation.api import build_router
from rag_qa.generation.service import Generator
from rag_qa.retrieval.types import (
    EmbedderMismatchError,
    EmptyCorpusError,
    RetrievalFilters,
    RetrievedChunk,
)
from test_generation_service import CHUNKS, FakeLLMClient


class StubRetriever:
    def __init__(
        self, chunks: Sequence[RetrievedChunk] | None = None, error: Exception | None = None
    ) -> None:
        self._chunks = list(chunks or CHUNKS)
        self._error = error
        self.calls: list[tuple[str, int, RetrievalFilters | None]] = []

    async def retrieve(
        self, query: str, k: int = 8, filters: RetrievalFilters | None = None
    ) -> list[RetrievedChunk]:
        self.calls.append((query, k, filters))
        if self._error is not None:
            raise self._error
        return self._chunks


def _app(retriever: Any, generator: Generator) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(retriever, generator))
    return app


async def _post(app: FastAPI, payload: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/ask", json=payload)


async def test_ask_returns_answer_verdict_and_citations() -> None:
    retriever = StubRetriever()
    app = _app(retriever, Generator(FakeLLMClient()))
    response = await _post(app, {"question": "What does Article 1 require?"})

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "answered"
    assert body["answer"] == "Providers must comply [1]."
    assert body["citations"][0]["section_path"] == CHUNKS[0].section_path
    assert body["citations"][0]["chunk_id"] == str(CHUNKS[0].chunk_id)
    assert body["usage"]["generator_identity"] == "anthropic:claude-sonnet-5"
    assert body["usage"]["prompt_tokens"] == 1200
    assert retriever.calls[0][0] == "What does Article 1 require?"


async def test_ask_passes_k_and_filters_through() -> None:
    retriever = StubRetriever()
    app = _app(retriever, Generator(FakeLLMClient()))
    document_id = str(uuid.uuid4())
    await _post(
        app,
        {
            "question": "Scoped question?",
            "k": 3,
            "filters": {"doc_types": ["regulation"], "document_ids": [document_id]},
        },
    )
    _, k, filters = retriever.calls[0]
    assert k == 3
    assert filters is not None
    assert filters.doc_types == ("regulation",)
    assert filters.document_ids == (uuid.UUID(document_id),)


@pytest.mark.parametrize(
    "error",
    [
        EmbedderMismatchError("corpus embedder does not match query embedder"),
        EmptyCorpusError("chunks table is empty"),
    ],
)
async def test_retrieval_faults_surface_as_503(error: Exception) -> None:
    """A corpus/embedder mismatch is an operational fault, not a bad request."""
    app = _app(StubRetriever(error=error), Generator(FakeLLMClient()))
    response = await _post(app, {"question": "Anything?"})
    assert response.status_code == 503
    assert str(error) in response.json()["detail"]


async def test_blank_question_is_422() -> None:
    app = _app(StubRetriever(), Generator(FakeLLMClient()))
    assert (await _post(app, {"question": "   "})).status_code == 422
    assert (await _post(app, {"question": ""})).status_code == 422


async def test_stream_events_follow_the_specified_order() -> None:
    client = FakeLLMClient(stream_slices=["ANSWE", "RED\nProviders comply [", "1]."])
    app = _app(StubRetriever(), Generator(client))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        http.stream("POST", "/ask", json={"question": "What applies?", "stream": True}) as response,
    ):
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.split("\n\n")
        if line.startswith("data: ")
    ]
    kinds = [event["type"] for event in events]
    assert kinds[0] == "verdict"
    assert kinds[-1] == "complete"
    assert "citation" in kinds

    text = "".join(event["text"] for event in events if event["type"] == "text")
    assert text == "Providers comply [1]."
    assert "ANSWERED" not in text  # the verdict token never reaches the client
    assert events[0]["verdict"] == "answered"
    assert events[-1]["completion_tokens"] == 80
