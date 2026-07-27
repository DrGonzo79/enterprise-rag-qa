"""The completion record and the failure signal (SPEC-008 AC-1 … AC-8).

Captured from the *configured handler's* rendered output, never `caplog` — see
`captured_logs` and CLAUDE.md rule 3. The point of every assertion here is what
an operator would actually see.
"""

import asyncio
import json
import pathlib
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import ADMIN_KEY, READ_KEY, StubRetriever, build_app, client_for, get, post
from conftest import SeededCorpus, StubQueryEmbedder
from rag_qa.generation.clients.base import TextChunk, Usage
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import EmbedderMismatchError
from test_api_budget import _cleanup, _log_spend
from test_api_context import captured_logs
from test_generation_service import FakeLLMClient


class ExplodingClient(FakeLLMClient):
    """Fails after the first token, once the response headers have gone out."""

    @asynccontextmanager
    async def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[AsyncIterator[TextChunk | Usage]]:
        self.calls.append((system, user, max_tokens))

        async def events() -> AsyncIterator[TextChunk | Usage]:
            yield TextChunk("ANSWERED\n")
            raise RuntimeError("provider died mid-stream")

        yield events()


QUESTION = {"question": "What applies?"}
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def records(sink: Any, msg: str = "http.request") -> list[dict[str, Any]]:
    return [
        line
        for line in (json.loads(raw) for raw in sink.getvalue().splitlines() if raw.strip())
        if line["msg"] == msg
    ]


# --- AC-1: the completion record exists and is singular ------------------------


async def test_a_successful_query_produces_exactly_one_record() -> None:
    app = build_app()
    with captured_logs() as sink:
        response = await post(app, "/query", QUESTION)

    assert response.status_code == 200
    completed = records(sink)
    assert len(completed) == 1
    entry = completed[0]
    assert entry["method"] == "POST"
    assert entry["route"] == "/query"  # the template, never a raw path
    assert entry["status"] == 200
    assert entry["duration_ms"] > 0
    assert entry["verdict"] == "answered"
    assert entry["request_id"] == response.headers["x-request-id"]
    assert entry["level"] == "INFO"


async def test_a_shed_request_is_recorded_rather_than_going_missing() -> None:
    """A request that failed early is not a request that goes unrecorded — and
    the shed path is the one uvicorn's access log measures worst, since the
    decision is made in middleware it brackets around."""
    app = build_app(
        StubRetriever(delay=0.4), query_acquire_timeout_seconds=0.05, max_concurrent_queries=1
    )
    with captured_logs() as sink:
        async with client_for(app) as http:
            responses = await asyncio.gather(
                *[http.post("/query", json=QUESTION, timeout=10) for _ in range(2)]
            )

    assert sorted(r.status_code for r in responses) == [200, 503]
    shed = [entry for entry in records(sink) if entry["status"] == 503]
    assert len(shed) == 1
    assert shed[0]["error_code"] == "overloaded"
    assert shed[0]["level"] == "WARNING"
    assert shed[0]["duration_ms"] > 0


async def test_an_unhandled_error_is_recorded_with_its_code() -> None:
    app = build_app(StubRetriever(error=RuntimeError("boom")))
    with captured_logs() as sink:
        response = await post(app, "/query", QUESTION)

    assert response.status_code == 500
    entry = next(e for e in records(sink) if e["status"] == 500)
    assert entry["error_code"] == "internal_error"
    assert entry["level"] == "WARNING"


async def test_a_budget_trip_is_recorded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"log-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        with captured_logs() as sink:
            response = await post(app, "/query", QUESTION)
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    entry = next(e for e in records(sink) if e["status"] == 503)
    assert entry["error_code"] == "budget_exhausted"


async def test_a_stream_is_recorded_when_it_ends_not_when_headers_are_sent() -> None:
    """The record has to carry the verdict its background pump resolved seconds
    after the response started, which is only possible if it is emitted last."""
    app = build_app()
    with captured_logs() as sink:
        async with (
            client_for(app) as http,
            http.stream("POST", "/query", json={**QUESTION, "stream": True}) as response,
        ):
            body = "".join([chunk async for chunk in response.aiter_text()])

    assert "complete" in body
    completed = records(sink)
    assert len(completed) == 1
    assert completed[0]["status"] == 200
    assert completed[0]["verdict"] == "answered"


# --- AC-2: the record carries nothing sensitive --------------------------------


@pytest.mark.parametrize("fmt", ["json", "text"])
async def test_no_record_carries_the_question_the_answer_or_a_key(fmt: str) -> None:
    question_canary = f"canary-q-{uuid.uuid4().hex}"
    app = build_app()
    with captured_logs(fmt=fmt) as sink:
        response = await post(app, "/query", {"question": question_canary})

    output = sink.getvalue()
    assert response.status_code == 200
    answer_canary = response.json()["answer"][:40]
    assert question_canary not in output
    assert answer_canary not in output
    assert READ_KEY not in output
    assert ADMIN_KEY not in output


# --- AC-3: probe endpoints do not flood ----------------------------------------


async def test_probes_are_counted_not_narrated() -> None:
    app = build_app()
    with captured_logs(level="INFO") as sink:
        for _ in range(10):
            await get(app, "/healthz", key=None)
    assert records(sink) == []

    with captured_logs(level="DEBUG") as sink:
        for _ in range(10):
            await get(app, "/healthz", key=None)
    assert len(records(sink)) == 10

    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    assert 'rag_qa_requests_total{endpoint="/healthz",status="200"} 20' in body


# --- AC-4, AC-5: the failure signal --------------------------------------------


async def test_the_three_refusals_are_distinguishable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The criterion the whole metric section exists for. Before this, a budget
    trip, a shed, and an embedder mismatch were `status="503"` counted three
    times, and the most consequential state of the deployment — the demo has
    stopped answering — could not be read from the endpoint an operator reads."""
    marker = f"log-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        await post(app, "/query", QUESTION)  # budget trip
    finally:
        await _cleanup(session_factory, marker)

    mismatch_app = build_app(StubRetriever(error=EmbedderMismatchError("a != b")))
    await post(mismatch_app, "/query", QUESTION)

    shed_app = build_app(
        StubRetriever(delay=0.4), query_acquire_timeout_seconds=0.05, max_concurrent_queries=1
    )
    async with client_for(shed_app) as http:
        await asyncio.gather(*[http.post("/query", json=QUESTION, timeout=10) for _ in range(2)])

    for target, expected in (
        (app, 'rag_qa_errors_total{code="budget_exhausted"} 1'),
        (mismatch_app, 'rag_qa_errors_total{code="embedder_mismatch"} 1'),
        (shed_app, 'rag_qa_errors_total{code="overloaded"} 1'),
    ):
        assert expected in (await get(target, "/metrics", key=ADMIN_KEY)).text

    budget_body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    assert 'rag_qa_budget_trips_total{ceiling="daily"} 1' in budget_body
    assert "rag_qa_requests_shed_total 0" in budget_body

    shed_body = (await get(shed_app, "/metrics", key=ADMIN_KEY)).text
    assert "rag_qa_requests_shed_total 1" in shed_body
    assert "rag_qa_budget_trips_total" in shed_body  # declared, not advanced
    assert "rag_qa_budget_trips_total{ceiling=" not in shed_body


async def test_budget_headroom_is_published_only_when_a_ceiling_exists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unbounded = build_app()
    assert (
        "rag_qa_budget_remaining_usd" not in (await get(unbounded, "/metrics", key=ADMIN_KEY)).text
    )

    app = build_app(session_factory=session_factory, monthly_budget_usd=Decimal("20.00"))
    await post(app, "/query", QUESTION)
    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    assert 'rag_qa_budget_remaining_usd{ceiling="daily"}' in body
    assert 'rag_qa_budget_remaining_usd{ceiling="monthly"}' in body


# --- AC-6: the exposition is valid, judged by something that is not us ---------


async def test_metrics_parse_with_the_prometheus_client_parser(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Hand-rolling a wire format and hoping it parses is not acceptable; the
    oracle is the library, the runtime is ours (KD-4)."""
    from prometheus_client.parser import text_string_to_metric_families

    app = build_app(session_factory=session_factory, monthly_budget_usd=Decimal("20.00"))
    await post(app, "/query", QUESTION)
    await post(app, "/query", {"question": "   "})  # populate errors_total
    await get(app, "/nope", key=None)

    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    families = list(text_string_to_metric_families(body))
    assert families
    names = [family.name for family in families]
    assert len(names) == len(set(names)), f"duplicate metric family: {names}"
    for family in families:
        assert family.documentation, f"{family.name} has no HELP"
        assert family.type, f"{family.name} has no TYPE"
    assert {"rag_qa_errors_total", "rag_qa_budget_remaining_usd"} <= {
        name + "_total" if name + "_total" in body else name for name in names
    }


# --- AC-7: a stream failing mid-flight is recorded -----------------------------


async def test_a_mid_stream_failure_leaves_a_server_side_record() -> None:
    """Before this, the client got a terminal error frame and the server kept
    nothing — the one failure a user experiences as a broken answer was the only
    one with no evidence."""
    app = build_app(client=ExplodingClient())
    with captured_logs() as sink:
        async with (
            client_for(app) as http,
            http.stream("POST", "/query", json={**QUESTION, "stream": True}) as response,
        ):
            body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.status_code == 200  # headers already went out
    assert '"type": "error"' in body or '"type":"error"' in body

    failures = records(sink, "stream failed after the response began")
    assert len(failures) == 1
    assert failures[0]["error_code"] == "upstream_error"
    assert failures[0]["request_id"] == response.headers["x-request-id"]
    assert failures[0]["level"] == "ERROR"
    assert len(records(sink)) == 1  # still exactly one completion record


async def test_a_client_disconnect_is_not_a_failure() -> None:
    """A disconnect is not a provider failure, and conflating them would make the
    ERROR record meaningless on exactly the deployment where clients wander off."""
    app = build_app()
    with captured_logs() as sink:
        async with (
            client_for(app) as http,
            http.stream("POST", "/query", json={**QUESTION, "stream": True}) as response,
        ):
            await response.aiter_raw().__anext__()  # take one chunk, then leave

    assert records(sink, "stream failed after the response began") == []


# --- AC-8: the walkthrough cannot go stale -------------------------------------


async def test_observability_doc_matches_what_is_actually_emitted(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_corpus: SeededCorpus,
) -> None:
    """A walkthrough written from memory names fields that are nearly right, and
    a reader who greps for one and finds nothing concludes the logging is broken
    rather than the doc.

    The sample has to cover every record the document shows, so all four are
    driven here: a real retrieval, a completion, a budget trip, and a stream that
    dies mid-flight. A narrower sample would let a renamed field in an
    infrequent record slip through — which is the exact drift this guards.
    """
    doc = (REPO_ROOT / "docs" / "observability.md").read_text()
    emitted: set[str] = set()

    def absorb(sink: Any) -> None:
        for raw in sink.getvalue().splitlines():
            if raw.strip():
                emitted.update(json.loads(raw))

    # 1. A real retrieval and a healthy completion record.
    app = build_app(
        Retriever(session_factory, StubQueryEmbedder()), session_factory=session_factory
    )
    with captured_logs() as sink:
        await post(app, "/query", {"question": "quarklebit"})
    absorb(sink)

    # 2. A budget trip: the figures record and an error completion record.
    marker = f"doc-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    tripped = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        with captured_logs() as sink:
            await post(tripped, "/query", QUESTION)
        absorb(sink)
    finally:
        await _cleanup(session_factory, marker)

    # 3. A stream that fails after the headers went out.
    with captured_logs() as sink:
        async with (
            client_for(build_app(client=ExplodingClient())) as http,
            http.stream("POST", "/query", json={**QUESTION, "stream": True}) as response,
        ):
            [chunk async for chunk in response.aiter_text()]
    absorb(sink)

    documented: set[str] = set()
    for block in re.findall(r"```json\n(.*?)```", doc, re.S):
        documented |= set(re.findall(r'"([a-z_]+)":', block))
    assert documented, "the doc shows no sample records"
    assert documented <= emitted, f"documented but never emitted: {sorted(documented - emitted)}"

    # Scraped from the app that has a ceiling configured: the headroom gauge is
    # deliberately absent when there is no budget to have headroom against.
    body = (await get(tripped, "/metrics", key=ADMIN_KEY)).text
    named = set(re.findall(r"\brag_qa_[a-z_]+\b", doc))
    assert named, "the doc names no metrics"
    missing = {name for name in named if name not in body}
    assert not missing, f"documented but never exposed: {sorted(missing)}"


def test_uvicorns_access_log_is_replaced_not_duplicated() -> None:
    """Two access logs per request is worse than either alone, and uvicorn's is
    the one that cannot carry the request id (KD-1)."""
    import logging

    from rag_qa.observability import configure_logging

    logging.getLogger("uvicorn.access").disabled = False
    configure_logging()
    assert logging.getLogger("uvicorn.access").disabled


# --- failure paths: the record must survive them, and must not guess ----------


async def test_a_bare_exception_still_yields_a_well_formed_500_and_one_record() -> None:
    """`observe_error` raises on an unregistered code by design. Nothing can
    reach it with one — the class check makes subclasses impossible, and a code
    assigned dynamically is overwritten with `internal_error` before the record
    is emitted — so an unhandled exception ends as an ordinary 500."""
    from fastapi import APIRouter

    app = build_app()
    router = APIRouter()

    @router.get("/bare")
    async def bare() -> None:
        raise ValueError("untranslated and unregistered")

    app.include_router(router)
    with captured_logs() as sink:
        async with client_for(app) as http:
            response = await http.get("/bare")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == response.headers["x-request-id"]
    assert error["presentation"] == "degraded"
    assert "ValueError" not in response.text  # no traceback, no type leak

    completed = records(sink)
    assert len(completed) == 1
    assert completed[0]["status"] == 500
    assert completed[0]["error_code"] == "internal_error"


async def test_an_error_assigned_an_unregistered_code_degrades_to_500() -> None:
    """The dynamic-assignment path, which no class check can cover."""
    from fastapi import APIRouter

    from rag_qa.api.errors import ApiError

    app = build_app()
    router = APIRouter()

    @router.get("/rogue")
    async def rogue() -> None:
        error = ApiError("boom")
        error.code = "never_registered"
        raise error

    app.include_router(router)
    async with client_for(app) as http:
        response = await http.get("/rogue")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


async def test_telemetry_failure_cannot_break_a_response() -> None:
    """`_finish` runs after `http.response.start` has gone out, so an exception
    there escapes as a protocol error on a request the caller was already
    answered. It is swallowed and logged instead."""
    app = build_app()

    def explode(_code: str) -> None:
        raise RuntimeError("metrics backend on fire")

    app.state.rag.metrics.observe_error = explode  # type: ignore[method-assign]

    with captured_logs() as sink:
        response = await post(app, "/query", {"question": "   "})  # 422 -> observe_error

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert any(
        line["msg"].startswith("failed to record the completion")
        for line in (json.loads(x) for x in sink.getvalue().splitlines() if x.strip())
    )


async def test_a_disconnect_is_recorded_but_is_not_distinguishable_from_completion() -> None:
    """Documented rather than guessed (SPEC-008 KD-3).

    Generation deliberately outlives the client connection — the tokens were
    spent whether or not anyone was listening — so the completion record reports
    what the *server* did, and a disconnect after a complete answer looks exactly
    like a delivered one. What distinguishes a provider failure is the separate
    ERROR record, not a field on this one.
    """
    app = build_app()
    with captured_logs() as sink:
        async with (
            client_for(app) as http,
            http.stream("POST", "/query", json={**QUESTION, "stream": True}) as response,
        ):
            await response.aiter_raw().__anext__()  # take one chunk, then leave

    completed = records(sink)
    assert len(completed) == 1, "a disconnect must still produce a record"
    assert completed[0]["status"] == 200
    assert completed[0]["verdict"] == "answered"
    # The honest part: there is no field here that says the client left.
    assert "client_disconnected" not in completed[0]
    assert records(sink, "stream failed after the response began") == []


def test_uvicorn_protocol_errors_stay_and_are_routed_through_the_formatter() -> None:
    """Only the access log is replaced. `uvicorn.error` carries the requests that
    never reached the ASGI app, and it is kept — routed here so one pipeline
    carries both rather than two formats."""
    import logging as stdlib_logging

    with captured_logs() as sink:
        stdlib_logging.getLogger("uvicorn.error").warning("Invalid HTTP request received.")

    lines = [json.loads(x) for x in sink.getvalue().splitlines() if x.strip()]
    assert [line["msg"] for line in lines] == ["Invalid HTTP request received."]
    assert lines[0]["logger"] == "uvicorn.error"
    assert lines[0]["request_id"] == ""  # correct: the request never reached us
