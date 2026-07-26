"""POST /query from SPEC-006 AC-1, AC-3, AC-4. No database, no provider."""

import uuid

import pytest

from api_harness import StubRetriever, build_app, post
from rag_qa.generation.clients.base import StopKind
from rag_qa.generation.types import Verdict
from rag_qa.retrieval.types import EmbedderMismatchError, EmptyCorpusError
from test_generation_service import CHUNKS, FakeLLMClient

# --- AC-1: JSON contract ------------------------------------------------------


async def test_query_returns_answer_verdict_citations_and_usage() -> None:
    retriever = StubRetriever()
    app = build_app(retriever)
    response = await post(app, "/query", {"question": "What does Article 1 require?"})

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "answered"
    assert body["answer"] == "Providers must comply [1]."
    assert body["dropped_markers"] == []

    citation = body["citations"][0]
    assert citation["section_path"] == CHUNKS[0].section_path
    assert citation["chunk_id"] == str(CHUNKS[0].chunk_id)
    assert citation["document_title"] == CHUNKS[0].document_title
    assert citation["doc_type"] == CHUNKS[0].doc_type

    usage = body["usage"]
    assert usage["generator_identity"] == "anthropic:claude-sonnet-5"
    assert usage["prompt_tokens"] == 1200
    assert usage["completion_tokens"] == 80
    # A string, never a float: cost_usd is numeric(10,6).
    assert isinstance(usage["cost_usd"], str)
    assert usage["prompt_version"]

    # request_id is echoed on the header and carried in the body.
    assert body["request_id"] == response.headers["x-request-id"]
    assert retriever.calls[0][0] == "What does Article 1 require?"


async def test_k_and_filters_reach_the_retriever_unchanged() -> None:
    retriever = StubRetriever()
    app = build_app(retriever)
    document_id = str(uuid.uuid4())
    await post(
        app,
        "/query",
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


# --- AC-3: error mapping ------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            EmbedderMismatchError("corpus embedder does not match query embedder"),
            "embedder_mismatch",
        ),
        (EmptyCorpusError("chunks table is empty"), "empty_corpus"),
    ],
)
async def test_retrieval_faults_are_503_with_a_named_code(error: Exception, code: str) -> None:
    """Operational faults, not bad requests: the corpus and the query embedder
    disagree, or there is no corpus at all."""
    app = build_app(StubRetriever(error=error))
    response = await post(app, "/query", {"question": "Anything?"})
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == code
    assert str(error) in body["message"]
    assert body["request_id"] == response.headers["x-request-id"]


async def test_blank_and_malformed_questions_are_422() -> None:
    app = build_app()
    # min_length=1 accepts "   ", so the handler has to reject it explicitly.
    blank = await post(app, "/query", {"question": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_error"

    for payload in ({"question": ""}, {}, {"question": "ok", "k": 0}, {"question": 7}):
        response = await post(app, "/query", payload)
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_error"


async def test_provider_transport_failure_is_502() -> None:
    class ExplodingClient(FakeLLMClient):
        async def complete(self, system: str, user: str, max_tokens: int):  # type: ignore[no-untyped-def]
            raise ConnectionError("connection reset by peer")

    app = build_app(client=ExplodingClient())
    response = await post(app, "/query", {"question": "What applies?"})
    assert response.status_code == 502
    body = response.json()["error"]
    assert body["code"] == "upstream_error"
    # Only the exception type crosses back — never the message or a traceback.
    assert "ConnectionError" in body["message"]
    assert "connection reset" not in body["message"]
    assert "Traceback" not in body["message"]


async def test_error_bodies_never_leak_internals() -> None:
    class LeakyRetriever(StubRetriever):
        async def retrieve(self, query, k=8, filters=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("SELECT * FROM chunks -- /Users/secret/path.py line 42")

    app = build_app(LeakyRetriever())
    response = await post(app, "/query", {"question": "Anything?"})
    assert response.status_code == 500
    body = response.text
    assert "SELECT" not in body
    assert "/Users/" not in body
    assert response.json()["error"]["code"] == "internal_error"


# --- AC-4: every verdict is 200 (Key decision 1) ------------------------------


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (FakeLLMClient("INSUFFICIENT_EVIDENCE\nNot covered."), Verdict.INSUFFICIENT_EVIDENCE),
        (FakeLLMClient(stop=StopKind.REFUSAL), Verdict.PROVIDER_REFUSED),
        (FakeLLMClient(stop=StopKind.MAX_TOKENS), Verdict.TRUNCATED),
        (FakeLLMClient("no verdict line at all"), Verdict.ERROR),
    ],
)
async def test_every_verdict_returns_200(client: FakeLLMClient, expected: Verdict) -> None:
    """Status describes the transport; verdict describes the outcome. A refusal
    is a scored capability, and a 4xx would encode it as a failure."""
    app = build_app(client=client)
    response = await post(app, "/query", {"question": "What applies?"})
    assert response.status_code == 200
    assert response.json()["verdict"] == str(expected)


async def test_zero_chunks_refuses_with_200_and_no_llm_call() -> None:
    client = FakeLLMClient()
    app = build_app(StubRetriever(chunks=[]), client)
    response = await post(app, "/query", {"question": "Unanswerable?"})
    assert response.status_code == 200
    assert response.json()["verdict"] == str(Verdict.INSUFFICIENT_EVIDENCE)
    assert client.calls == []  # an absence, not a threshold — no request spent
