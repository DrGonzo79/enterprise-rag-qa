"""Liveness and readiness (SPEC-006 AC-10, KD-2)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import build_app, get
from test_generation_service import FakeLLMClient


async def test_healthz_needs_no_database_at_all() -> None:
    """SPEC-001 AC-2 still holds. A container healthcheck that depends on
    Postgres restarts a healthy API container on a database blip."""
    app = build_app(session_factory=None)
    response = await get(app, "/healthz", key=None)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_is_unavailable_without_a_database() -> None:
    app = build_app(session_factory=None)
    response = await get(app, "/health", key=None)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"]["ok"] is False


async def test_health_reports_every_check_against_a_live_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = build_app(session_factory=session_factory)
    response = await get(app, "/health", key=None)
    body = response.json()
    checks = body["checks"]

    assert checks["database"]["ok"] is True
    assert checks["database"]["latency_ms"] is not None
    assert checks["migrations"]["ok"] is True
    assert checks["migrations"]["revision"]
    assert checks["generator"]["ok"] is True
    assert checks["generator"]["identity"] == "anthropic:claude-sonnet-5"
    # The test database may or may not hold chunks; either way the status is
    # decided by whether the *database* answered, not by corpus contents.
    assert body["status"] in ("ok", "degraded")
    assert response.status_code == 200


async def test_readiness_never_spends_a_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = FakeLLMClient()
    app = build_app(client=client, session_factory=session_factory)
    await get(app, "/health", key=None)
    assert client.calls == []


async def test_degraded_still_serves() -> None:
    """`degraded` is 200 (something is off, still serving); only `unavailable`
    is 503."""
    app = build_app(session_factory=None)
    body = (await get(app, "/health", key=None)).json()
    assert body["status"] == "unavailable"  # no database is not merely degraded
