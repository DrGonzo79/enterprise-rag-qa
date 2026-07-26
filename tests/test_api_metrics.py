"""Prometheus metrics (SPEC-006 AC-12, KD-8, KD-9)."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import ADMIN_KEY, READ_KEY, build_app, get, post


async def test_metrics_requires_the_admin_key() -> None:
    """Against the Prometheus convention, which assumes a private network this
    deployment does not have."""
    app = build_app()
    assert (await get(app, "/metrics", key=None)).status_code == 401
    assert (await get(app, "/metrics", key=READ_KEY)).status_code == 403
    assert (await get(app, "/metrics", key=ADMIN_KEY)).status_code == 200


async def test_exposes_requests_verdicts_tokens_and_cost() -> None:
    app = build_app()
    await post(app, "/query", {"question": "What applies?"})
    body = (await get(app, "/metrics", key=ADMIN_KEY)).text

    assert 'rag_qa_requests_total{endpoint="/query",status="200"} 1' in body
    assert 'rag_qa_verdicts_total{verdict="answered"} 1' in body
    assert "rag_qa_prompt_tokens_total 1200" in body
    assert "rag_qa_completion_tokens_total 80" in body
    assert "rag_qa_cost_usd_total " in body
    assert "rag_qa_query_latency_seconds_count 1" in body
    assert 'rag_qa_query_latency_seconds_bucket{le="+Inf"} 1' in body
    # Prometheus requires HELP/TYPE for a well-formed exposition.
    assert body.count("# TYPE") >= 5


async def test_counters_advance_across_requests() -> None:
    app = build_app()
    for _ in range(3):
        await post(app, "/query", {"question": "What applies?"})
    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    assert 'rag_qa_verdicts_total{verdict="answered"} 3' in body
    assert "rag_qa_prompt_tokens_total 3600" in body


async def test_a_scrape_opens_no_database_connection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Monitoring must never compete with serving for the scarcest resource
    (KD-9) — and the RESERVED arithmetic in KD-10 depends on this being zero."""
    app = build_app(session_factory=session_factory)
    engine = session_factory.kw["bind"]
    checkouts = 0

    def on_checkout(*_args: object) -> None:
        nonlocal checkouts
        checkouts += 1

    event.listen(engine.sync_engine, "checkout", on_checkout)
    try:
        response = await get(app, "/metrics", key=ADMIN_KEY)
    finally:
        event.remove(engine.sync_engine, "checkout", on_checkout)

    assert response.status_code == 200
    assert checkouts == 0


async def test_errors_are_counted_by_status() -> None:
    app = build_app()
    await post(app, "/query", {"question": "   "})  # 422
    await get(app, "/metrics", key=None)  # 401
    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    assert 'rag_qa_requests_total{endpoint="/query",status="422"} 1' in body
    assert 'rag_qa_requests_total{endpoint="/metrics",status="401"} 1' in body
