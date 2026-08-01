"""GET /metrics — Prometheus text format, admin-scoped (SPEC-006 KD-8, KD-9).

Authenticated against the convention. The convention assumes a private network
or a separate port; this is a single Container App with public ingress, so
"unauthenticated" here means "public" — and an unauthenticated cost counter in
front of a metered LLM API is a real-time feedback channel for anyone trying to
burn the budget.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response

from rag_qa.api.auth import Scope, require
from rag_qa.api.deps import AppState

router = APIRouter()

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get(
    "/metrics",
    summary="Prometheus metrics (admin key required)",
    dependencies=[Depends(require(Scope.ADMIN))],
    response_class=Response,
    responses={200: {"content": {PROMETHEUS_CONTENT_TYPE: {"schema": {"type": "string"}}}}},
)
async def metrics(request: Request) -> Response:
    state: AppState = request.app.state.rag
    # Cached totals only. `snapshot` is synchronous by design, so a scrape cannot
    # query however the guard evolves (KD-9, AC-12), and it returns None before
    # the first refresh rather than publishing an unspent ceiling as headroom.
    state.metrics.set_budget_snapshot(state.budget.snapshot(datetime.now(UTC)))
    return Response(content=state.metrics.render(), media_type=PROMETHEUS_CONTENT_TYPE)
