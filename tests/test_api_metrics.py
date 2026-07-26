"""Prometheus metrics (SPEC-006 AC-12, KD-8, KD-9)."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import ADMIN_KEY, READ_KEY, build_app, client_for, get, post
from rag_qa.api.middleware import UNMATCHED_ROUTE


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


# --- label cardinality (SPEC-006 AC-16) ---------------------------------------


async def test_unmatched_paths_collapse_to_one_series() -> None:
    """The regression test for a defect, not a refinement.

    The original middleware labelled with `scope["path"]` verbatim. Its premise —
    a fixed, small set of paths — holds for matched routes and fails completely
    for the 404 space: every distinct unmatched path created a key in a
    process-lifetime Counter that is never evicted, reachable with no
    authentication, in the process that also enforces the spend ceiling. It is an
    unbounded-memory vector before it is a metrics problem.
    """
    app = build_app()
    metrics = app.state.rag.metrics
    before = len(metrics.requests)

    async with client_for(app) as http:
        for i in range(50):
            response = await http.get(f"/no-such-path-{i}")
            assert response.status_code == 404

    assert len(metrics.requests) - before == 1, (
        f"50 distinct unmatched paths produced {len(metrics.requests) - before} series; "
        "the label space must be bounded"
    )
    assert (UNMATCHED_ROUTE, 404) in metrics.requests
    assert metrics.requests[(UNMATCHED_ROUTE, 404)] == 50

    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    assert f'endpoint="{UNMATCHED_ROUTE}"' in body
    assert "no-such-path" not in body


async def test_matched_routes_are_labelled_with_their_template() -> None:
    """Collapsing everything would be bounded and useless — the matched routes
    must still be distinguishable from each other and from the 404 space."""
    app = build_app()
    await post(app, "/query", {"question": "What applies?"})
    await get(app, "/healthz", key=None)
    await get(app, "/openapi.json", key=None)

    labels = {label for label, _ in app.state.rag.metrics.requests}
    assert {"/query", "/healthz", "/openapi.json"} <= labels
    assert UNMATCHED_ROUTE not in labels


async def test_the_label_space_is_enumerable_from_the_route_table() -> None:
    """The rule the original reasoning was missing: a label is safe when its
    value space is enumerable from the code."""
    app = build_app()
    async with client_for(app) as http:
        for path in ("/query", "/healthz", "/health", "/metrics", "/docs", "/openapi.json"):
            await http.get(path)
            await http.post(path, json={"question": "What applies?"})
        for i in range(20):
            await http.get(f"/{i}/{i}/{i}")

    labels = {label for label, _ in app.state.rag.metrics.requests}
    assert labels <= _registered_paths(app.routes) | {UNMATCHED_ROUTE}


def _registered_paths(routes: object) -> set[str]:
    """Flatten the route table.

    Included routers are wrapped rather than spliced in, so their routes hang off
    `original_router` — the recursion follows both that and any nested `routes`.
    """
    found: set[str] = set()
    for route in routes if isinstance(routes, list) else []:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            found.add(path)
        for nested in (getattr(route, "routes", None), getattr(route, "original_router", None)):
            found |= _registered_paths(getattr(nested, "routes", nested))
    return found
