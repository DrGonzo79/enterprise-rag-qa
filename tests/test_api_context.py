"""Request-id threading and the log configuration that renders it (SPEC-006
AC-7).

The id must reach the log records SPEC-004 and SPEC-005 already emit, without
either module gaining a parameter — that is the whole point of KD-5 — **and it
must reach an operator**, which is the half that shipped missing.
"""

import inspect
import io
import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import StubRetriever, build_app, client_for, post
from conftest import SeededCorpus, StubQueryEmbedder
from rag_qa.api.context import request_id_var, sanitize_request_id
from rag_qa.generation.service import Generator
from rag_qa.observability import JsonFormatter, configure_logging
from rag_qa.retrieval.service import Retriever


@contextmanager
def captured_logs(*, level: str = "INFO", fmt: str = "json") -> Iterator[io.StringIO]:
    """Capture what the *configured handler* writes — not LogRecord objects.

    Deliberately not `caplog`: caplog reads records before formatting, which is
    exactly how the previous AC-7 test passed while no formatter existed.
    """
    sink = io.StringIO()
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_pkg_level = logging.getLogger("rag_qa").level
    root.handlers = []
    try:
        configure_logging(level=level, fmt=fmt, stream=sink)
        yield sink
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_root_level)
        logging.getLogger("rag_qa").setLevel(saved_pkg_level)


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


async def test_id_reaches_formatted_output_from_the_real_retriever(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_corpus: SeededCorpus,
) -> None:
    """AC-7, asserted on **rendered output** rather than on LogRecord objects.

    The earlier version of this test read `caplog.records`, which are records
    before any formatter runs — so it proved the half of KD-5 that worked
    (the id is on the record) and was structurally blind to the half that did
    not: no formatter was configured anywhere, and the id reached no operator.
    A test that inspects an intermediate object instead of the output is the
    shape to be most suspicious of (CLAUDE.md rule 3).

    The real `Retriever`, not a stub: its record is emitted from inside the
    `asyncio.gather` branches, so this also proves ContextVar propagation into
    tasks — the load-bearing assumption KD-5 rests on.
    """
    app = build_app(
        Retriever(session_factory, StubQueryEmbedder()), session_factory=session_factory
    )
    with captured_logs(level="INFO") as sink:
        async with client_for(app) as http:
            response = await http.post(
                "/query",
                json={"question": "quarklebit"},
                headers={"X-Request-ID": "trace-me-42"},
            )

    assert response.status_code == 200
    assert response.json()["request_id"] == "trace-me-42"

    lines = [json.loads(line) for line in sink.getvalue().splitlines() if line.strip()]
    assert lines, "nothing was written to the configured handler at all"
    emitted = {line["logger"] for line in lines}
    assert "rag_qa.retrieval.service" in emitted, "SPEC-004's Retriever emitted no record"
    # Every line, including the ones SPEC-004 and SPEC-005 emit, carries the id.
    assert {line["request_id"] for line in lines} == {"trace-me-42"}
    # And the structured fields survive formatting rather than being flattened
    # into the message, which is what makes them queryable at all.
    retrieval = next(line for line in lines if line["logger"] == "rag_qa.retrieval.service")
    assert retrieval["result_count"] >= 0
    assert "query_sha" in retrieval


async def test_output_is_one_json_line_per_record_even_with_newlines() -> None:
    """A message containing a newline must not split one logical record across
    two lines — the same framing argument SPEC-006 KD-3 made for SSE."""
    with captured_logs(level="INFO") as sink:
        logging.getLogger("rag_qa.test").warning('first\nsecond\ttab "quoted"')
    lines = [line for line in sink.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["msg"] == 'first\nsecond\ttab "quoted"'


async def test_log_level_is_honored() -> None:
    """`LOG_LEVEL` sat in .env.example read by nothing until this was fixed."""
    with captured_logs(level="WARNING") as sink:
        logging.getLogger("rag_qa.test").info("invisible")
        logging.getLogger("rag_qa.test").warning("visible")
    messages = [json.loads(line)["msg"] for line in sink.getvalue().splitlines() if line.strip()]
    assert messages == ["visible"]


async def test_text_format_is_human_readable_and_still_carries_the_id() -> None:
    with captured_logs(level="INFO", fmt="text") as sink:
        token = request_id_var.set("text-mode-7")
        try:
            logging.getLogger("rag_qa.test").info("hello")
        finally:
            request_id_var.reset(token)
    output = sink.getvalue()
    assert "text-mode-7" in output and "hello" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.splitlines()[0])


def test_configure_logging_is_idempotent() -> None:
    """Called by every create_app(); a second call must not double every line."""
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers = []
    try:
        configure_logging()
        assert len(root.handlers) == 1
        configure_logging()
        configure_logging()
        assert len(root.handlers) == 1
    finally:
        root.handlers = saved


def test_create_app_configures_logging() -> None:
    """The defect was shipping the record factory without anything that renders
    it — so the factory *and* the configuration are asserted at the same seam."""
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers = []
    try:
        build_app()
        assert len(root.handlers) == 1, "create_app() installed no log handler"
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert logging.getLogger("rag_qa").level == logging.INFO
    finally:
        root.handlers = saved


def test_exception_records_carry_a_structured_error() -> None:
    with captured_logs(level="INFO") as sink:
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("rag_qa.test").exception("failed")
    payload = json.loads(sink.getvalue().splitlines()[0])
    assert payload["error"]["type"] == "ValueError"
    assert "ValueError: boom" in payload["error"]["stack"]


def test_no_library_import_configures_logging() -> None:
    """A library that configures the root logger on import steals a decision
    from its caller — it fights pytest's handler and duplicates uvicorn's."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging, sys;"
            "import rag_qa.retrieval, rag_qa.generation, rag_qa.ingest, rag_qa.db;"
            "sys.stdout.write(str(len(logging.getLogger().handlers)))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "0"


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
