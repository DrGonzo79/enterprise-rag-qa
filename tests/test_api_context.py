"""Request-id threading (SPEC-006 AC-7).

The id must reach the log records SPEC-004 and SPEC-005 already emit, without
either module gaining a parameter — that is the whole point of KD-5.
"""

import inspect
import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import StubRetriever, build_app, client_for, post
from conftest import SeededCorpus, StubQueryEmbedder
from rag_qa.api.context import sanitize_request_id
from rag_qa.generation.service import Generator
from rag_qa.retrieval.service import Retriever


async def test_generated_id_is_echoed_on_the_header_and_in_the_body() -> None:
    app = build_app()
    response = await post(app, "/query", {"question": "What applies?"})
    request_id = response.headers["x-request-id"]
    assert request_id
    assert response.json()["request_id"] == request_id


async def test_inbound_id_is_reused_when_well_formed() -> None:
    app = build_app()
    async with client_for(app) as http:
        response = await http.post(
            "/query", json={"question": "What applies?"}, headers={"X-Request-ID": "abc-123"}
        )
    assert response.headers["x-request-id"] == "abc-123"
    assert response.json()["request_id"] == "abc-123"


@pytest.mark.parametrize(
    "hostile",
    ["x" * 500, "abc\ndef", "has spaces", "semi;colon", ""],
    ids=["too-long", "newline", "space", "punctuation", "empty"],
)
async def test_hostile_inbound_ids_are_replaced_not_rejected(hostile: str) -> None:
    """Attacker-controlled text headed for log records: newlines forge entries.
    Replaced silently — failing a request over a cosmetic header would be worse."""
    app = build_app()
    async with client_for(app) as http:
        response = await http.post(
            "/query", json={"question": "What applies?"}, headers={"X-Request-ID": hostile}
        )
    assert response.status_code == 200
    assert response.headers["x-request-id"] != hostile
    assert "\n" not in response.headers["x-request-id"]


def test_sanitizer_accepts_the_documented_grammar() -> None:
    assert sanitize_request_id("01J-abc_9.x:y") == "01J-abc_9.x:y"
    assert sanitize_request_id(None) != ""
    assert len(sanitize_request_id("!" * 10)) > 0


async def test_id_reaches_records_from_the_real_retriever(
    caplog: pytest.LogCaptureFixture,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_corpus: SeededCorpus,
) -> None:
    """The real Retriever, not a stub: its log record is emitted from inside the
    `asyncio.gather` branches, so this also proves ContextVar propagation into
    tasks — the load-bearing assumption KD-5 rests on."""
    app = build_app(
        Retriever(session_factory, StubQueryEmbedder()), session_factory=session_factory
    )
    with caplog.at_level(logging.INFO, logger="rag_qa.retrieval.service"):
        caplog.clear()
        async with client_for(app) as http:
            response = await http.post(
                "/query",
                json={"question": "quarklebit"},
                headers={"X-Request-ID": "trace-me-42"},
            )

    assert response.status_code == 200
    records = [r for r in caplog.records if r.name == "rag_qa.retrieval.service"]
    assert records, "SPEC-004's Retriever emitted no log record"
    assert {r.request_id for r in records} == {"trace-me-42"}  # type: ignore[attr-defined]
    assert response.json()["request_id"] == "trace-me-42"


async def test_records_outside_a_request_have_an_empty_id() -> None:
    """Ambient state, correctly empty — not a bug to be 'fixed' with a default."""
    build_app()  # installs the record factory
    record = logging.getLogger("rag_qa.test").makeRecord(
        "rag_qa.test", logging.INFO, __file__, 1, "outside", (), None
    )
    assert record.request_id == ""  # type: ignore[attr-defined]


def test_neither_library_gained_a_request_id_parameter() -> None:
    """KD-5's constraint, asserted on the signatures rather than trusted."""
    for func in (Retriever.retrieve, Generator.answer, Generator.stream_answer):
        assert "request_id" not in inspect.signature(func).parameters, func.__qualname__


async def test_error_responses_carry_the_id_too() -> None:
    app = build_app(StubRetriever(error=RuntimeError("boom")))
    response = await post(app, "/query", {"question": "Anything?"})
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
