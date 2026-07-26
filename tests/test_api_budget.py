"""Daily spend ceiling and circuit breaker (SPEC-006 AC-14, KD-16)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import build_app, post
from rag_qa.api.budget import SpendGuard, seconds_until_utc_midnight, utc_day_start
from rag_qa.db.models import QueryLog
from rag_qa.generation.prompt import PROMPT_VERSION
from test_generation_service import FakeLLMClient

QUESTION = "What applies?"


async def _log_spend(
    factory: async_sessionmaker[AsyncSession], marker: str, cost: str, created_at: datetime
) -> None:
    async with factory() as session:
        session.add(
            QueryLog(
                id=uuid.uuid4(),
                question=f"{marker} {created_at.isoformat()}",
                provider="anthropic",
                model="claude-sonnet-5",
                latency_ms=10,
                prompt_tokens=1000,
                completion_tokens=100,
                cost_usd=Decimal(cost),
                retrieved_chunk_ids=[],
                answer_text="x",
                verdict="answered",
                prompt_version=PROMPT_VERSION,
                created_at=created_at,
            )
        )
        await session.commit()


async def _cleanup(factory: async_sessionmaker[AsyncSession], marker: str) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM query_log WHERE question LIKE :q"), {"q": f"{marker}%"}
        )
        await session.commit()


# --- off by default -----------------------------------------------------------


async def test_disabled_guard_opens_no_connection() -> None:
    guard = SpendGuard(None, daily_limit_usd=None)
    assert not guard.enabled
    await guard.check()
    assert guard.refresh_count == 0


async def test_query_serves_normally_with_no_ceiling_configured() -> None:
    app = build_app()
    assert (await post(app, "/query", {"question": QUESTION})).status_code == 200


# --- the breaker --------------------------------------------------------------


async def test_breaker_trips_before_the_provider_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A breaker that trips after paying for the answer is not a breaker."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    client = FakeLLMClient()
    app = build_app(
        client=client,
        session_factory=session_factory,
        daily_budget_usd=Decimal("5.00"),
    )
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "budget_exhausted"
    assert client.calls == []  # never reached the provider
    assert int(response.headers["retry-after"]) > 0


async def test_breaker_is_an_error_envelope_not_a_canned_answer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A canned answer in the shape of a real one would teach a viewer that the
    system answered when it did not — and would land in query_log as an answer
    no model produced."""
    marker = f"budget-{uuid.uuid4()}"
    question = f"refused-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        response = await post(app, "/query", {"question": question})
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text("SELECT count(*) FROM query_log WHERE question = :q"), {"q": question}
                )
            ).scalar_one()
    finally:
        await _cleanup(session_factory, marker)

    assert "verdict" not in response.json()
    assert "error" in response.json()
    assert rows == 0  # no row written for a request that never happened


async def test_spend_is_scoped_to_the_utc_day(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    question = f"{marker} probe"
    yesterday = utc_day_start(datetime.now(UTC)) - timedelta(hours=1)
    await _log_spend(session_factory, marker, "9.00", yesterday)
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        # Yesterday's spend does not count against today's ceiling.
        assert (await post(app, "/query", {"question": question})).status_code == 200
        await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
        app_today = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
        assert (await post(app_today, "/query", {"question": question})).status_code == 503
    finally:
        # The successful call above logged its own row under the same marker.
        await _cleanup(session_factory, marker)


# --- caching ------------------------------------------------------------------


async def test_one_aggregate_per_ttl_window_not_one_per_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The property that keeps the ceiling off the hot path."""
    guard = SpendGuard(session_factory, daily_limit_usd=Decimal("100"), refresh_seconds=1000.0)
    for _ in range(5):
        await guard.check()
    assert guard.refresh_count == 1


async def test_local_delta_trips_inside_the_same_ttl_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spend since the last refresh counts immediately — otherwise the breaker
    would wait a full TTL after crossing the limit, and the overshoot would be
    unbounded rather than bounded by spend_rate x TTL x replicas."""
    import pytest

    from rag_qa.api.errors import BudgetExhausted

    guard = SpendGuard(session_factory, daily_limit_usd=Decimal("1.00"), refresh_seconds=1000.0)
    await guard.check()  # under the limit
    guard.record(Decimal("1.50"))
    with pytest.raises(BudgetExhausted):
        await guard.check()
    assert guard.refresh_count == 1  # no extra query needed to notice


def test_retry_after_counts_down_to_utc_midnight() -> None:
    just_before = datetime(2026, 7, 26, 23, 59, 0, tzinfo=UTC)
    assert seconds_until_utc_midnight(just_before) == 60
    assert seconds_until_utc_midnight(datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)) == 86400
