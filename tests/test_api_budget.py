"""Monthly cap, derived daily ceiling, and the circuit breaker (SPEC-006 AC-14,
AC-15, AC-20, AC-21, AC-22, KD-16)."""

import asyncio
import calendar
import logging
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api_harness import build_app, client_for, post, settings
from rag_qa.api.budget import (
    CENT,
    PRESSURE_RETRY_AFTER_SECONDS,
    Reservation,
    SpendGuard,
    derive_daily_limit,
    next_utc_month_start,
    seconds_until_utc_midnight,
    seconds_until_utc_month_end,
    utc_day_start,
    utc_month_start,
)
from rag_qa.api.deps import ConfigurationError
from rag_qa.api.errors import BudgetExhausted, BudgetPressure, Overloaded
from rag_qa.db.models import QueryLog, SpendSource
from rag_qa.generation.clients.base import LLMResult, TextChunk, Usage
from rag_qa.generation.pricing import compute_cost
from rag_qa.generation.prompt import PROMPT_VERSION
from rag_qa.generation.service import Generator
from rag_qa.retrieval.types import RetrievedChunk
from test_generation_service import CHUNKS, FakeLLMClient

QUESTION = "What applies?"


def assert_no_figures(message: str) -> None:
    """The error body must name no dollar amounts.

    SPEC-006 KD-8 keeps the cost meter behind the admin key because an
    unauthenticated spend number is a live progress bar for anyone trying to
    drain the budget. A 503 body naming the ceiling, the override, and the
    derived value was that same meter, reachable by any caller who can trigger
    the error — a side channel around the scope that was carefully chosen.
    """
    assert "$" not in message, message
    assert not re.search(r"\d+\.\d{2}", message), message


async def _log_spend(
    factory: async_sessionmaker[AsyncSession],
    marker: str,
    cost: str,
    created_at: datetime,
    source: SpendSource = SpendSource.VISITOR,
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
                source=source.value,
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
    # $5 of headroom above what today already holds, not an absolute $5: the
    # first assertion is that today's window is *clear*, and an absolute ceiling
    # would make that a statement about `query_log`'s history instead. Measured
    # **once**, before the row below lands -- re-measuring afterwards would move
    # the ceiling up by the very spend the second assertion is about.
    ceiling = await _spend_so_far_today(session_factory) + Decimal("5.00")
    app = build_app(session_factory=session_factory, daily_budget_usd=ceiling)
    try:
        # Yesterday's spend does not count against today's ceiling.
        assert (await post(app, "/query", {"question": question})).status_code == 200
        await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
        app_today = build_app(session_factory=session_factory, daily_budget_usd=ceiling)
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
    would wait a full TTL after crossing the limit, and one replica's overshoot
    would be a function of the TTL rather than of one query (KD-16 amendment 5)."""

    guard = _empty_window_guard(
        session_factory, daily_limit_usd=Decimal("1.00"), refresh_seconds=1000.0
    )
    await guard.check()  # under the limit
    guard.record(Decimal("1.50"))
    with pytest.raises(BudgetExhausted):
        await guard.check()
    assert guard.refresh_count == 1  # no extra query needed to notice


def test_retry_after_counts_down_to_utc_midnight() -> None:
    just_before = datetime(2026, 7, 26, 23, 59, 0, tzinfo=UTC)
    assert seconds_until_utc_midnight(just_before) == 60
    assert seconds_until_utc_midnight(datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)) == 86400


# --- the monthly cap, and the daily ceiling derived from it --------------------


@pytest.mark.parametrize(
    ("year", "month", "days"),
    [(2026, 2, 28), (2028, 2, 29), (2026, 4, 30), (2026, 7, 31)],
)
def test_every_month_length_divides_and_never_overspends(year: int, month: int, days: int) -> None:
    """28/29/30/31, including a leap February — the divisor is this month's real
    length, so a full month at the ceiling lands on the budget rather than past
    it in short months and short of it in long ones."""
    at = datetime(year, month, 5, tzinfo=UTC)
    derived = derive_daily_limit(Decimal("20.00"), at)
    assert derived * days <= Decimal("20.00")
    # And close to it: flooring to the cent may cost at most a cent per day.
    assert derived * days > Decimal("20.00") - (CENT * days)
    # The month window is exactly as long as the month.
    assert (next_utc_month_start(at) - utc_month_start(at)).days == days
    assert seconds_until_utc_month_end(utc_month_start(at)) == days * 86400


def test_utc_is_the_definition_so_dst_cannot_move_the_boundary() -> None:
    """The rollover is UTC, everywhere. UTC observes no daylight saving, so every
    budget day is exactly 86400 seconds — a local-time ceiling would have a
    23-hour day and a 25-hour day each year, one silently tightening the ceiling
    and the other silently loosening it."""
    ny = ZoneInfo("America/New_York")
    berlin = ZoneInfo("Europe/Berlin")

    # US spring-forward 2026-03-08 02:00 local; EU 2026-03-29 02:00 local.
    for local_now in (
        datetime(2026, 3, 8, 1, 59, tzinfo=ny),  # minutes before the US shift
        datetime(2026, 3, 8, 3, 1, tzinfo=ny),  # minutes after
        datetime(2026, 11, 1, 1, 30, tzinfo=ny),  # US fall-back, ambiguous local hour
        datetime(2026, 3, 29, 1, 59, tzinfo=berlin),
        datetime(2026, 3, 29, 3, 1, tzinfo=berlin),
    ):
        day = utc_day_start(local_now)
        assert day.tzinfo == UTC
        assert day == local_now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        # Every budget day is 86400 seconds, across every transition.
        assert seconds_until_utc_midnight(day) == 86400

    # The same instant expressed in three zones yields one identical window.
    instant = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    assert (
        utc_day_start(instant)
        == utc_day_start(instant.astimezone(ny))
        == utc_day_start(instant.astimezone(berlin))
    )


def test_a_naive_datetime_is_rejected_rather_than_assumed() -> None:
    """`astimezone()` reads a naive datetime as system-local, which shifts the
    whole ceiling by the host's UTC offset — a wrong window that looks right on
    a UTC developer machine and is wrong in deployment."""
    with pytest.raises(ValueError, match="aware datetime"):
        utc_day_start(datetime(2026, 7, 26, 12, 0))
    with pytest.raises(ValueError, match="aware datetime"):
        utc_month_start(datetime(2026, 7, 26, 12, 0))


def test_day_boundary_is_exact_at_the_edges() -> None:
    assert utc_day_start(datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)) == datetime(
        2026, 7, 26, tzinfo=UTC
    )
    assert utc_day_start(datetime(2026, 7, 26, 23, 59, 59, 999999, tzinfo=UTC)) == datetime(
        2026, 7, 26, tzinfo=UTC
    )
    # One microsecond into the next day rolls the window over.
    assert utc_day_start(datetime(2026, 7, 27, 0, 0, 0, tzinfo=UTC)) == datetime(
        2026, 7, 27, tzinfo=UTC
    )
    # Month rollover at the year boundary.
    assert utc_month_start(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == datetime(
        2026, 12, 1, tzinfo=UTC
    )
    assert next_utc_month_start(datetime(2028, 2, 29, tzinfo=UTC)) == datetime(
        2028, 3, 1, tzinfo=UTC
    )


def test_daily_is_derived_from_the_monthly_budget() -> None:
    """The monthly figure is the one an owner commits to; the daily ceiling is a
    consequence of it, not an independent choice with a surprise attached."""
    # 31 days: $20 / 31 = $0.645..., floored to the cent.
    assert derive_daily_limit(Decimal("20.00"), datetime(2026, 7, 26, tzinfo=UTC)) == Decimal(
        "0.64"
    )
    # 28 days: the divisor is this month's length, not a nominal 30, so a full
    # month at the ceiling lands on the budget rather than past it.
    assert derive_daily_limit(Decimal("28.00"), datetime(2026, 2, 10, tzinfo=UTC)) == Decimal(
        "1.00"
    )
    # A month at the derived ceiling never exceeds the monthly budget.
    for month, days in ((1, 31), (2, 28), (4, 30)):
        derived = derive_daily_limit(Decimal("20.00"), datetime(2026, month, 5, tzinfo=UTC))
        assert derived * days <= Decimal("20.00")


def test_derived_daily_never_rounds_to_zero() -> None:
    """A budget too small to divide should refuse loudly, not serve as if
    unconfigured — a zero ceiling that never trips is the failure mode this
    whole decision exists to avoid."""
    assert derive_daily_limit(Decimal("0.10"), datetime(2026, 7, 26, tzinfo=UTC)) == Decimal("0.01")


def test_explicit_daily_override_is_capped_at_twice_the_derived_ceiling() -> None:
    """An override is a burst shape, not a second budget. Uncapped, $5/day
    against a $20 month drains it in four days with no error until the monthly
    trips — the ceiling failing at exactly its job."""
    at = datetime(2026, 7, 26, tzinfo=UTC)  # 31 days: derived $0.64, cap $1.28
    derived = SpendGuard(None, monthly_limit_usd=Decimal("20.00"))
    assert derived.daily_limit_for(at) == Decimal("0.64")

    # Below the derived ceiling: honored as given.
    under = SpendGuard(None, daily_limit_usd=Decimal("0.40"), monthly_limit_usd=Decimal("20.00"))
    assert under.daily_limit_for(at) == Decimal("0.40")

    # Between derived and the cap: honored — a deliberate launch-day burst.
    burst = SpendGuard(None, daily_limit_usd=Decimal("1.00"), monthly_limit_usd=Decimal("20.00"))
    assert burst.daily_limit_for(at) == Decimal("1.00")

    # Above the cap: capped, not honored.
    footgun = SpendGuard(None, daily_limit_usd=Decimal("5.00"), monthly_limit_usd=Decimal("20.00"))
    effective = footgun.daily_limit_for(at)
    assert effective == Decimal("1.28") == 2 * Decimal("0.64")

    # The cap is what makes the drain time bounded: >= 15 days, not 4.
    assert effective is not None and Decimal("20.00") / effective > 15


def test_the_cap_boundary_is_inclusive() -> None:
    """Exactly 2x derived is honored; a cent above it is capped.

    Pinned explicitly because `min()` makes the boundary invisible in the code —
    a reader cannot tell from `min(daily, 2 * derived)` whether the operator who
    typed the cap value exactly gets what they typed, and that is precisely the
    value someone reading the documentation will type.
    """
    at = datetime(2026, 7, 26, tzinfo=UTC)  # 31 days: derived $0.64, cap $1.28
    exact = SpendGuard(None, daily_limit_usd=Decimal("1.28"), monthly_limit_usd=Decimal("20.00"))
    assert exact.daily_limit_for(at) == Decimal("1.28")
    # ...and it is reported as an honored burst, not as a capped value.
    assert "capped" not in exact._daily_shape(at)[1]

    over = SpendGuard(None, daily_limit_usd=Decimal("1.29"), monthly_limit_usd=Decimal("20.00"))
    assert over.daily_limit_for(at) == Decimal("1.28")
    assert "capped" in over._daily_shape(at)[1]


def test_the_warning_boundary_is_exclusive_of_derived() -> None:
    """At exactly the derived ceiling there is nothing to warn about — the
    override and the derivation agree. A cent above it, the operator has chosen
    to spend the month faster than uniformly and should be told so."""
    monthly = Decimal("20.00")
    derived = derive_daily_limit(monthly, datetime.now(UTC))
    for override, expected_records in ((derived, 0), (derived + CENT, 1)):
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        deps_logger = logging.getLogger("rag_qa.api.deps")
        deps_logger.addHandler(handler)
        try:
            settings(daily_budget_usd=override, monthly_budget_usd=monthly).require_serving(
                needs_providers=False
            )
        finally:
            deps_logger.removeHandler(handler)
        assert len(records) == expected_records, f"override ${override} vs derived ${derived}"


def test_the_cap_moves_with_the_month_length() -> None:
    """Derived is per-month, so the cap is too — February's is higher because
    its days are fewer, not because the budget changed."""
    guard = SpendGuard(None, daily_limit_usd=Decimal("99"), monthly_limit_usd=Decimal("28.00"))
    assert guard.daily_limit_for(datetime(2026, 2, 10, tzinfo=UTC)) == Decimal("2.00")  # 28 days
    assert guard.daily_limit_for(datetime(2026, 7, 10, tzinfo=UTC)) == Decimal("1.80")  # 31 days


def test_override_above_derived_warns_at_startup_naming_both_numbers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="rag_qa.api.deps"):
        settings(
            daily_budget_usd=Decimal("5.00"), monthly_budget_usd=Decimal("20.00")
        ).require_serving(needs_providers=False)

    fields = next(r for r in caplog.records if r.name == "rag_qa.api.deps").__dict__
    assert fields["override_usd"] == "5.00"
    assert fields["derived_usd"] == "0.64"
    assert fields["cap_usd"] == "1.28"
    assert fields["effective_usd"] == "1.28"
    assert fields["monthly_usd"] == "20.00"
    assert fields["min_days_to_drain_month"] >= 15


def test_no_warning_when_the_override_is_at_or_below_derived(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="rag_qa.api.deps"):
        settings(
            daily_budget_usd=Decimal("0.50"), monthly_budget_usd=Decimal("20.00")
        ).require_serving(needs_providers=False)
    assert [r for r in caplog.records if r.name == "rag_qa.api.deps"] == []


async def test_capped_override_is_the_ceiling_actually_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The cap is enforced on the serving path, not merely reported at startup:
    spend above the cap but below the requested override still trips."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "2.00", datetime.now(UTC))
    client = FakeLLMClient()
    app = build_app(
        client=client,
        session_factory=session_factory,
        daily_budget_usd=Decimal("5.00"),  # capped to ~2x derived, well under $2.00
        monthly_budget_usd=Decimal("20.00"),
    )
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    assert client.calls == []
    assert_no_figures(response.json()["error"]["message"])


def test_daily_above_monthly_is_rejected_at_startup() -> None:
    """Incoherent rather than merely generous: the day it is reached, the month
    is already over."""
    with pytest.raises(ConfigurationError, match="exceeds"):
        settings(
            daily_budget_usd=Decimal("50.00"), monthly_budget_usd=Decimal("20.00")
        ).require_serving(needs_providers=False)


async def test_monthly_cap_trips_over_http_before_the_provider_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "25.00", datetime.now(UTC))
    client = FakeLLMClient()
    app = build_app(
        client=client, session_factory=session_factory, monthly_budget_usd=Decimal("20.00")
    )
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "budget_exhausted"
    assert client.calls == []
    # Which ceiling tripped is operator information, not caller information.
    assert_no_figures(response.json()["error"]["message"])


async def test_monthly_window_sees_spend_the_daily_window_cannot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The exposure a daily ceiling structurally cannot bound: quiet days that
    add up. Spend sits at the start of the month and the clock is moved to
    mid-month, so today's window is empty by construction on any calendar date.
    """

    marker = f"budget-{uuid.uuid4()}"
    month_start = utc_month_start(datetime.now(UTC))
    mid_month = month_start + timedelta(days=15, hours=9)
    await _log_spend(session_factory, marker, "25.00", month_start)
    try:
        daily_only = SpendGuard(
            session_factory, daily_limit_usd=Decimal("5.00"), now=lambda: mid_month
        )
        # mid_month is a different UTC day from the one ambient rows land on, so
        # this window is empty by construction -- except on the 16th, which is
        # why the assertion below is about a *cleared* window rather than a
        # small one.
        await daily_only.check()  # today is clear — the daily ceiling sees nothing

        monthly = SpendGuard(
            session_factory, monthly_limit_usd=Decimal("20.00"), now=lambda: mid_month
        )
        with pytest.raises(BudgetExhausted) as raised:
            await monthly.check()
        assert raised.value.ceiling == "monthly"
    finally:
        await _cleanup(session_factory, marker)


async def test_spend_before_this_month_does_not_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    last_month = utc_month_start(datetime.now(UTC)) - timedelta(hours=1)
    await _log_spend(session_factory, marker, "500.00", last_month)
    app = await _headroom_app(session_factory, monthly=Decimal("20.00"))
    try:
        response = await post(app, "/query", {"question": f"{marker} probe"})
    finally:
        await _cleanup(session_factory, marker)
    assert response.status_code == 200


async def test_both_windows_come_from_one_statement(pooled_engine: AsyncEngine) -> None:
    """Adding the monthly ceiling must not add a connection consumer: the budget
    refresh is deliberately *not* in RESERVED_CONNECTIONS, and concurrency.py's
    arithmetic assumes one single-checkout aggregate per TTL."""
    statements: list[str] = []

    def record(*args: Any) -> None:
        statement = args[2]
        if "query_log" in statement:
            statements.append(statement)

    guard = SpendGuard(
        async_sessionmaker(pooled_engine, expire_on_commit=False),
        daily_limit_usd=Decimal("100"),
        monthly_limit_usd=Decimal("1000"),
        refresh_seconds=1000.0,
    )
    event.listen(pooled_engine.sync_engine, "before_cursor_execute", record)
    try:
        for _ in range(3):
            await guard.check()
    finally:
        event.remove(pooled_engine.sync_engine, "before_cursor_execute", record)

    assert guard.refresh_count == 1
    assert len(statements) == 1, "the two windows must be one aggregate, not two"


async def test_monthly_is_reported_when_both_ceilings_are_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A Retry-After that expires into another 503 is worse than an honest long
    one — "resets at midnight" is false when the month is gone."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "25.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, monthly_budget_usd=Decimal("20.00"))
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    # The monthly reset is what is reported, and it is reported as a clock the
    # caller can act on rather than as a ceiling they can meter.
    assert_no_figures(response.json()["error"]["message"])
    now = datetime.now(UTC)
    assert int(response.headers["retry-after"]) == pytest.approx(
        seconds_until_utc_month_end(now), abs=5
    )


def test_retry_after_counts_down_to_the_next_utc_month() -> None:
    assert seconds_until_utc_month_end(datetime(2026, 7, 31, 23, 59, 0, tzinfo=UTC)) == 60
    assert next_utc_month_start(datetime(2026, 12, 15, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    assert next_utc_month_start(datetime(2026, 1, 31, tzinfo=UTC)) == datetime(
        2026, 2, 1, tzinfo=UTC
    )


async def test_the_503_body_names_no_figures_and_the_log_names_all_of_them(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reconciliation: the caller learns *that* and *when*, the operator
    learns *how much*. Both from one trip."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        with caplog.at_level(logging.WARNING, logger="rag_qa.api.budget"):
            response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    error = response.json()["error"]
    assert_no_figures(error["message"])
    assert "ceiling" not in error["message"]  # which window tripped is operator info
    # ...and the presentation seam tells a client to render the explanatory
    # state rather than an error page, without keeping its own list.
    assert error["presentation"] == "explanatory"
    assert error["reset"] == "window"
    assert int(response.headers["retry-after"]) > 0

    fields = next(r for r in caplog.records if r.msg == "spend ceiling reached").__dict__
    assert fields["ceiling"] == "daily"
    assert fields["limit_usd"] == "5.00"
    assert Decimal(fields["spent_usd"]) >= Decimal("9.00")
    assert fields["resets_at"]
    assert fields["request_id"] == response.headers["x-request-id"]


# --- reservations: the in-flight overshoot (AC-20, AC-21, AC-22, KD-16 am. 5) --
#
# The blind spot these close: `check()` ran before the provider call and
# `record()` after it, so every request being answered right now was invisible to
# every check happening right now. Not bounded by the TTL (it exists at TTL zero)
# and not bounded by KD-10's semaphore (released before the provider call by
# design) — so it grew with arrival rate against a ceiling of cents.


class ObservingClient(FakeLLMClient):
    """Reads the guard from *inside* the provider call.

    The only vantage point from which "a reservation is held while the call is in
    flight" is a real instant. Asserting before and after the request would prove
    only that the number returns to zero, which a guard that never reserved
    anything would also satisfy.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.guard: SpendGuard | None = None
        self.reserved_during_call: Decimal | None = None

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        assert self.guard is not None
        self.reserved_during_call = self.guard.reserved
        return await super().complete(system, user, max_tokens)


class RaisingClient(FakeLLMClient):
    """Fails the way a provider fails: during the call, after the reservation."""

    def __init__(self, error: BaseException, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._error = error

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        self.calls.append((system, user, max_tokens))
        raise self._error


class SlowClient(FakeLLMClient):
    """Holds the call open long enough for a second request to meet the first."""

    def __init__(self, delay: float = 0.2, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._delay = delay

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        await asyncio.sleep(self._delay)
        return await super().complete(system, user, max_tokens)


class FailingStreamClient(FakeLLMClient):
    """Emits a frame, then dies — the shape that reaches the pump's except."""

    @asynccontextmanager
    async def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[AsyncIterator[TextChunk | Usage]]:
        self.calls.append((system, user, max_tokens))

        async def events() -> AsyncIterator[TextChunk | Usage]:
            yield TextChunk("ANSWERED\nPartial")
            raise RuntimeError("provider dropped the stream")

        yield events()


class HangingStreamClient(FakeLLMClient):
    """Emits a frame and then never finishes, so the pump can be cancelled while
    it genuinely holds a reservation."""

    @asynccontextmanager
    async def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[AsyncIterator[TextChunk | Usage]]:
        self.calls.append((system, user, max_tokens))

        async def events() -> AsyncIterator[TextChunk | Usage]:
            yield TextChunk("ANSWERED\nPartial")
            await asyncio.Event().wait()

        yield events()


class WorstCaseClient(FakeLLMClient):
    """Reports the largest usage this call could possibly have billed: every
    prompt token the project's own tokenizer finds, and the full output
    allowance. What `max_cost` claims to bound, actually happening."""

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        from rag_qa.ingest.chunker import count_tokens

        self._prompt_tokens = count_tokens(system) + count_tokens(user)
        self._completion_tokens = max_tokens
        return await super().complete(system, user, max_tokens)


def _budgeted_app(
    client: FakeLLMClient, factory: async_sessionmaker[AsyncSession], **kw: Any
) -> Any:
    return build_app(client=client, session_factory=factory, **kw)


def _empty_window_guard(factory: async_sessionmaker[AsyncSession], **kw: Any) -> SpendGuard:
    """A guard whose UTC windows are empty whatever `query_log` already holds.

    Reservation arithmetic is about exact quantities — "reserved is back to
    exactly zero", "admitted x worst_case <= ceiling + worst_case" — so these
    tests cannot be written against whatever spend the database happens to
    contain from earlier tests or an earlier run. Pointing the clock at a future
    UTC month gives a window that is empty by construction rather than by luck.
    """
    future = datetime.now(UTC) + timedelta(days=400)
    return SpendGuard(factory, now=lambda: future, **kw)


async def _spend_so_far(factory: async_sessionmaker[AsyncSession]) -> tuple[Decimal, Decimal]:
    """(today, this month) as `query_log` already holds them.

    The app's guard reads the real clock and cannot be given a fake one through
    `create_app`, so an HTTP test that needs "N dollars of headroom" has to
    measure the floor rather than assume it is zero.
    """
    now = datetime.now(UTC)
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT coalesce(sum(cost_usd) FILTER (WHERE created_at >= :day), 0) AS day, "
                    "       coalesce(sum(cost_usd), 0) AS month "
                    "FROM query_log WHERE created_at >= :month"
                ),
                {"day": utc_day_start(now), "month": utc_month_start(now)},
            )
        ).one()
    return Decimal(str(row.day)), Decimal(str(row.month))


async def _spend_so_far_today(factory: async_sessionmaker[AsyncSession]) -> Decimal:
    return (await _spend_so_far(factory))[0]


async def _headroom_app(
    factory: async_sessionmaker[AsyncSession],
    *,
    daily: Decimal | None = None,
    monthly: Decimal | None = None,
    **kw: Any,
) -> Any:
    """An app whose ceiling is `headroom` **above what the window already
    contains**, rather than an absolute figure that assumes an empty database.

    Every HTTP test that expects a request to be *admitted* carries a premise:
    that this UTC day (or month) has not already spent the ceiling the test
    configures. Written as `daily_budget_usd=Decimal("5.00")` that premise is
    unstated, unenforced, and drifts — `query_log` in the test database only
    grows, so the test passes until one day it does not, for a reason that has
    nothing to do with what it asserts. Stating it as headroom makes the premise
    part of the setup instead of a fact about history.

    (The guard-level tests solve the same problem differently, with
    `_empty_window_guard` — an injected clock pointed at a window that is empty
    by construction. That is not available here: `create_app` builds its own
    `SpendGuard` on the real clock.)
    """
    day_total, month_total = await _spend_so_far(factory)
    if daily is not None:
        kw["daily_budget_usd"] = day_total + daily
    if monthly is not None:
        # A monthly-only budget implies a **derived** daily ceiling
        # (`monthly / days-in-month`), so headroom here is two-dimensional:
        # clearing the month's floor is not enough if today's floor still
        # exceeds the daily figure derived from it. Found by flooding today's
        # window with $50 and watching a monthly-headroom app trip on its
        # derived $2.26 daily -- the premise was still hiding, one level down.
        days = calendar.monthrange(datetime.now(UTC).year, datetime.now(UTC).month)[1]
        kw["monthly_budget_usd"] = max(month_total + monthly, days * (day_total + monthly))
    return build_app(session_factory=factory, **kw)


# --- AC-20: the claim is held across the call and released however it ends -----


async def test_the_reservation_is_held_during_the_call_and_settled_to_actual_after(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    client = ObservingClient()
    app = await _headroom_app(session_factory, client=client, daily=Decimal("5.00"))
    guard: SpendGuard = app.state.rag.budget
    client.guard = guard
    try:
        response = await post(app, "/query", {"question": f"{marker} probe"})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 200
    # Held while the provider call was in flight...
    assert client.reserved_during_call is not None
    assert client.reserved_during_call > 0
    # ...and given back exactly, not approximately.
    assert guard.reserved == Decimal("0")
    # Settled down to what was actually spent, not left at the worst case.
    actual = Decimal(response.json()["usage"]["cost_usd"])
    assert guard.recorded == actual
    assert client.reserved_during_call > actual  # the reservation was the bound


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RuntimeError("provider exploded"), 502),
        (Overloaded("shed inside generation", retry_after=1), 503),
    ],
    ids=["provider-exception", "translated-api-error"],
)
async def test_the_reservation_is_released_when_generation_fails(
    session_factory: async_sessionmaker[AsyncSession], error: Exception, status: int
) -> None:
    """A dead request holding a phantom debit makes the replica under-serve until
    the process restarts — silently, and worse the longer it runs. Each path is
    asserted on its own, because a release written on the success path covers
    none of them, and these two leave the route by different `raise` statements.
    """
    client = RaisingClient(error)
    app = await _headroom_app(session_factory, client=client, daily=Decimal("5.00"))
    guard: SpendGuard = app.state.rag.budget

    response = await post(app, "/query", {"question": QUESTION})

    assert response.status_code == status
    assert client.calls != [], "the provider call must have been attempted"
    assert guard.reserved == Decimal("0")


async def test_a_disconnected_client_does_not_leave_a_claim_behind(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Cancellation is the shape a client disconnect takes: the server task is
    torn down mid-await, inside the provider call, with no exception the route
    handles. Nothing but an unconditional `finally` releases here."""
    client = SlowClient(delay=5.0)
    app = await _headroom_app(session_factory, client=client, daily=Decimal("5.00"))
    guard: SpendGuard = app.state.rag.budget

    inflight = asyncio.create_task(post(app, "/query", {"question": QUESTION}))
    await asyncio.sleep(0.2)
    assert guard.reserved > 0, "the request must actually be mid-call when cancelled"

    inflight.cancel()
    with pytest.raises(asyncio.CancelledError):
        await inflight

    assert guard.reserved == Decimal("0")


async def test_a_stream_that_dies_after_its_first_frame_releases_its_reservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from rag_qa.api.routes import query as query_route

    client = FailingStreamClient()
    app = await _headroom_app(session_factory, client=client, daily=Decimal("5.00"))
    guard: SpendGuard = app.state.rag.budget

    async with (
        client_for(app) as http,
        http.stream("POST", "/query", json={"question": QUESTION, "stream": True}) as r,
    ):
        body = "".join([chunk async for chunk in r.aiter_text()])

    await asyncio.gather(*list(query_route._background), return_exceptions=True)
    assert "upstream_error" in body  # the failure did reach the client, in-band
    assert guard.reserved == Decimal("0")


async def test_a_cancelled_stream_releases_its_reservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The client-disconnect shape. Generation deliberately outlives the
    connection (the tokens were spent whether or not anyone listened), so the
    reservation is owned by the pump task and has to survive the teardown of the
    response — and be released when the task itself is torn down."""
    from rag_qa.api.routes import query as query_route

    client = HangingStreamClient()
    app = await _headroom_app(session_factory, client=client, daily=Decimal("5.00"))
    guard: SpendGuard = app.state.rag.budget
    before = set(query_route._background)

    # The request is driven from a task rather than awaited: httpx's ASGI
    # transport collects the whole response before returning, so a stream that
    # has not finished cannot be observed from the calling side at all.
    request = asyncio.create_task(post(app, "/query", {"question": QUESTION, "stream": True}))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if guard.reserved > 0:
            break
    # The pump is mid-generation, and the claim is real right now.
    assert guard.reserved > 0, "the request must be holding a claim before it is torn down"

    pending = [t for t in query_route._background if t not in before]
    assert pending, "the pump task must be the thing holding the claim"
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    assert guard.reserved == Decimal("0")
    # The cancelled pump still closed the stream, so the caller is not hung.
    assert (await request).status_code == 200


async def test_a_question_with_no_chunks_reserves_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SPEC-005 KD-6 answers a zero-chunk question without a provider call, so
    holding headroom for it would refuse other callers on behalf of spend that
    cannot happen."""
    from api_harness import StubRetriever

    marker = f"budget-{uuid.uuid4()}"
    client = ObservingClient()
    app = await _headroom_app(
        session_factory,
        retriever=StubRetriever(chunks=[]),
        client=client,
        daily=Decimal("5.00"),
    )
    guard: SpendGuard = app.state.rag.budget
    client.guard = guard
    try:
        response = await post(app, "/query", {"question": f"{marker} probe"})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 200
    assert response.json()["verdict"] == "insufficient_evidence"
    assert client.calls == []
    assert guard.reserved == Decimal("0")
    assert Generator(FakeLLMClient()).max_cost(QUESTION, []) == Decimal("0")


def _citation_dense_chunks(count: int = 40, repeats: int = 24) -> list[RetrievedChunk]:
    """Chunks whose text tokenizes far denser than prose does.

    Ordinary prose runs about four bytes per token, which is exactly the ratio a
    plausible-looking *estimate* would use — so a bound exercised only against
    prose passes whether it is a bound or an estimate. Regulatory text is not
    uniformly prose either: citation-dense passages, article-number tables, and
    the breadcrumb separator all tokenize denser than the average, which is
    where an estimate would quietly under-reserve.
    """
    body = "› §1(a)(ii) – Art. 6(2); Annex III, point 5(b). " * repeats
    return [
        replace(CHUNKS[0], chunk_id=uuid.uuid4(), ordinal=index, text=body)
        for index in range(count)
    ]


@pytest.mark.parametrize(
    "chunks", [CHUNKS, _citation_dense_chunks()], ids=["prose", "citation-dense"]
)
async def test_the_reserved_bound_is_never_below_what_the_call_actually_billed(
    chunks: Sequence[RetrievedChunk],
) -> None:
    """The bound, proved against a call that actually bills the worst case rather
    than against the arithmetic that produced the bound. `max_cost` promises
    `ceiling + one worst case`; if a real call can exceed it, that promise is the
    same shape as the one this amendment replaced.

    **The citation-dense case exists because the prose case could not fail.**
    Mutating the byte bound into `len(prompt) // 4` — a perfectly plausible
    estimate — left this test green: with four short chunks the output term
    dominates so completely that the input term could be wrong by any factor and
    the total still cleared the actual cost. That is CLAUDE.md rule 3's failure
    shape exactly, and it was found by breaking the behaviour rather than by
    reading the test.
    """
    client = WorstCaseClient()
    generator = Generator(client)
    answer = await generator.answer(QUESTION, chunks)

    assert generator.max_cost(QUESTION, chunks) >= answer.cost_usd
    assert answer.completion_tokens == 4096  # the output cap really was billed


async def test_the_input_half_of_the_bound_is_actually_under_test() -> None:
    """The guard on the test above: with the dense chunks, what the call bills
    for *input* exceeds what it bills for output, so the input bound is load
    bearing rather than hidden behind the output cap."""
    chunks = _citation_dense_chunks()
    client = WorstCaseClient()
    answer = await Generator(client).answer(QUESTION, chunks)

    input_cost = compute_cost(answer.generator_identity, answer.prompt_tokens, 0)
    output_cost = compute_cost(answer.generator_identity, 0, answer.completion_tokens)
    assert input_cost > output_cost, "the input term must dominate or the bound is untested"


def test_the_byte_bound_holds_for_the_tokenizer_this_project_uses() -> None:
    """Why bytes rather than a tokenizer: every BPE merge replaces two tokens
    with one, so no encoding can exceed the UTF-8 byte count. Asserted against
    real corpus-shaped text, including non-ASCII — the breadcrumb separator in
    every chunk is a multi-byte character, which is exactly where a
    character-count version of this bound would have been wrong."""
    from rag_qa.generation.prompt import SYSTEM_PROMPT, render_context
    from rag_qa.ingest.chunker import count_tokens

    prompt = SYSTEM_PROMPT + render_context(QUESTION, CHUNKS)
    assert "›" in prompt  # multi-byte, so bytes > characters here
    assert count_tokens(prompt) <= len(prompt.encode("utf-8"))


# --- AC-21: what a refresh clears, and what it must not ------------------------


async def test_reservations_survive_a_refresh_and_recorded_spend_does_not(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Getting this backwards fails in both directions and silently: keeping the
    delta double-counts spend that the refreshed total already contains, and
    clearing the reservations forgets every call in flight — which is the blind
    spot reservations exist to close, re-opened once per TTL."""
    marker = f"budget-{uuid.uuid4()}"
    clock = [1000.0]
    future = datetime.now(UTC) + timedelta(days=400)
    await _log_spend(session_factory, marker, "1.00", future)
    try:
        guard = SpendGuard(
            session_factory,
            daily_limit_usd=Decimal("5.00"),
            refresh_seconds=30.0,
            now=lambda: future,
            monotonic=lambda: clock[0],
        )
        await guard.check()
        assert guard.refresh_count == 1
        before = guard.snapshot(future)
        assert before is not None and before.remaining["daily"] == Decimal("4.00")

        reservation = await guard.reserve(Decimal("0.50"))
        guard.record(Decimal("0.02"))
        # That recorded spend has reached query_log, as it has by the time
        # `record()` is called on the serving path: the generator writes its row
        # before the route sees the answer.
        await _log_spend(session_factory, marker, "0.02", future)

        clock[0] += 31.0  # past the TTL: the next check refreshes
        await guard.check()
        assert guard.refresh_count == 2

        assert guard.reserved == Decimal("0.50"), "a call in flight was forgotten"
        assert guard.recorded == Decimal("0"), "spend already in query_log was counted twice"

        # And the total is right, counted exactly once: the ledger grew by $0.02
        # and headroom fell by $0.02, not by $0.04.
        after = guard.snapshot(future)
        assert after is not None
        assert after.remaining["daily"] == before.remaining["daily"] - Decimal("0.02")
        assert after.reserved == Decimal("0.50")

        reservation.settle(Decimal("0.01"))
        assert guard.reserved == Decimal("0")
        assert guard.recorded == Decimal("0.01")
    finally:
        await _cleanup(session_factory, marker)


async def test_settle_and_release_are_both_idempotent_and_never_lose_spend(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`finally: release()` runs after `settle()` on the success path, so the
    second call must give nothing back a second time — a negative reserved total
    would let the ceiling be exceeded by however many requests double-released."""
    guard = _empty_window_guard(
        session_factory, daily_limit_usd=Decimal("5.00"), refresh_seconds=1000.0
    )
    reservation = await guard.reserve(Decimal("0.30"))
    assert guard.reserved == Decimal("0.30")

    reservation.settle(Decimal("0.01"))
    reservation.release()
    reservation.release()

    assert guard.reserved == Decimal("0")
    assert guard.recorded == Decimal("0.01")


# --- AC-22: pressure is not exhaustion -----------------------------------------


async def test_reserve_distinguishes_committed_headroom_from_spent_money(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The distinction has to be made where the condition is chosen, because
    `envelope()` renders from the code alone — so a wrong choice here cannot be
    corrected downstream."""
    guard = _empty_window_guard(
        session_factory, daily_limit_usd=Decimal("1.00"), refresh_seconds=1000.0
    )

    await guard.reserve(Decimal("0.60"))
    await guard.reserve(Decimal("0.60"))  # admitted: outstanding was 0.60 < 1.00

    # Nothing has been spent. The money is claimed, not gone.
    assert guard.recorded == Decimal("0")
    with pytest.raises(BudgetPressure) as pressure:
        await guard.reserve(Decimal("0.10"))
    assert pressure.value.ceiling == "daily"
    assert pressure.value.retry_after == PRESSURE_RETRY_AFTER_SECONDS

    # Now the money really is gone, on the same guard, at the same instant.
    guard.record(Decimal("1.50"))
    with pytest.raises(BudgetExhausted):
        await guard.reserve(Decimal("0.10"))


async def test_a_request_is_never_refused_by_its_own_reservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A ceiling smaller than one worst-case query must still answer one question
    at a time. Counting the incoming claim against itself would refuse every
    request forever — an outage manufactured by the guard rather than by the
    budget, and most readily on the small budgets this is for."""
    guard = _empty_window_guard(
        session_factory, daily_limit_usd=Decimal("0.01"), refresh_seconds=1000.0
    )
    reservation = await guard.reserve(Decimal("5.00"))  # far above the ceiling
    reservation.settle(Decimal("0.001"))
    # ...and again, because the first one settled cheaply.
    await guard.reserve(Decimal("5.00"))


async def test_one_replica_cannot_exceed_the_ceiling_by_more_than_one_worst_case(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The claim that replaced "at most spend_rate x TTL x replicas".

    It is arithmetic over the guard's own admissions rather than a statement
    about traffic: no term here grows with arrival rate, which is the property
    the old sentence claimed and did not have.
    """
    ceiling = Decimal("1.00")
    worst_case = Decimal("0.07")
    guard = _empty_window_guard(session_factory, daily_limit_usd=ceiling, refresh_seconds=1000.0)

    admitted = 0
    while True:
        try:
            await guard.reserve(worst_case)
        except (BudgetPressure, BudgetExhausted):
            break
        admitted += 1

    assert admitted > 0
    # Every admitted request could bill its worst case and the day still lands
    # within one query of the ceiling.
    assert admitted * worst_case <= ceiling + worst_case
    # And the bound is tight rather than trivially satisfied by refusing early:
    # admitting one fewer would have left headroom unspent.
    assert (admitted + 1) * worst_case > ceiling


async def test_pressure_over_http_says_shortly_and_never_the_midnight_clock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Rendering this as `budget_exhausted` would hand a visitor a countdown to
    UTC midnight for a condition that clears in seconds — the same untruthfulness
    the fourth review round removed, arriving through a new door."""
    marker = f"budget-{uuid.uuid4()}"
    question = f"{marker} probe"
    # A ceiling exactly one worst-case query above what today has already spent.
    # Both terms are measured rather than guessed: the worst case so that a rate
    # change cannot quietly turn this into a test of nothing, and the floor so
    # that the app's guard -- which reads the real clock and cannot be given a
    # fake one -- sees headroom for the first request and none for the second.
    worst_case = Generator(FakeLLMClient()).max_cost(question, CHUNKS)
    assert worst_case > 0
    ceiling = await _spend_so_far_today(session_factory) + worst_case

    client = SlowClient(delay=0.3)
    app = _budgeted_app(client, session_factory, daily_budget_usd=ceiling)
    try:
        first = asyncio.create_task(post(app, "/query", {"question": question}))
        await asyncio.sleep(0.1)  # the first request is inside the provider call
        second = await post(app, "/query", {"question": question})
        first_response = await first
    finally:
        await _cleanup(session_factory, marker)

    assert first_response.status_code == 200
    assert second.status_code == 503
    error = second.json()["error"]
    assert error["code"] == "budget_pressure"
    assert error["presentation"] == "transient"
    assert error["reset"] == "shortly"
    assert int(second.headers["retry-after"]) == PRESSURE_RETRY_AFTER_SECONDS
    # No instant is quoted, and no figure either (AC-18's rule is not per-code).
    assert "resets at" not in error["message"]
    assert_no_figures(error["message"])
    # One provider call, not two: the refusal happened before the second one.
    assert len(client.calls) == 1


async def test_an_exhausted_budget_still_says_window_with_the_real_clock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The other half of the pair: adding a transient sibling must not soften the
    condition that genuinely does reset at a known instant."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    error = response.json()["error"]
    assert error["code"] == "budget_exhausted"
    assert error["presentation"] == "explanatory"
    assert error["reset"] == "window"
    assert int(response.headers["retry-after"]) == pytest.approx(
        seconds_until_utc_midnight(datetime.now(UTC)), abs=5
    )


async def test_pressure_is_counted_in_the_failure_signal_but_is_not_a_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SPEC-008's counters: `rag_qa_budget_trips_total` means the demo ran out of
    money, and diluting it with a three-second condition would cost it the one
    meaning an operator pages on."""
    marker = f"budget-{uuid.uuid4()}"
    question = f"{marker} probe"
    ceiling = await _spend_so_far_today(session_factory) + Generator(FakeLLMClient()).max_cost(
        question, CHUNKS
    )
    client = SlowClient(delay=0.3)
    app = _budgeted_app(client, session_factory, daily_budget_usd=ceiling)
    metrics = app.state.rag.metrics
    try:
        first = asyncio.create_task(post(app, "/query", {"question": question}))
        await asyncio.sleep(0.1)
        await post(app, "/query", {"question": question})
        await first
    finally:
        await _cleanup(session_factory, marker)

    assert metrics.errors["budget_pressure"] == 1
    assert metrics.budget_trips == {}
    assert metrics.requests_shed == 0  # nor is it a concurrency shed


async def test_metrics_publishes_committed_headroom_beside_remaining(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`remaining` keeps meaning money spent, so an alert threshold written
    against it keeps its meaning; committed headroom is a second series."""
    guard = _empty_window_guard(
        session_factory, daily_limit_usd=Decimal("5.00"), refresh_seconds=1000.0
    )
    await guard.check()
    await guard.reserve(Decimal("0.25"))

    snapshot = guard.snapshot(datetime.now(UTC))
    assert snapshot is not None
    assert snapshot.reserved == Decimal("0.25")
    assert snapshot.remaining["daily"] == Decimal("5.00")  # nothing has been spent

    from rag_qa.api.metrics import Metrics

    metrics = Metrics()
    metrics.set_budget_snapshot(snapshot)
    rendered = metrics.render()
    assert "rag_qa_budget_reserved_usd 0.25" in rendered
    assert 'rag_qa_budget_remaining_usd{ceiling="daily"} 5.00' in rendered


async def test_concurrent_reserves_cannot_admit_against_the_same_stale_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The bound holds only if test-and-add cannot yield in the middle.

    `ceiling + one worst-case query` is arithmetic over a comparison and an
    increment. If anything suspends between them, N coroutines read the same
    outstanding total, all decide there is room, and all take it — the in-flight
    blind spot rebuilt inside the mechanism that closed it, and invisible,
    because every individual request behaved correctly.

    Today no line in `_admit` awaits, so this passes whether or not the property
    is *structural*. It is `def` rather than `async def` for exactly that reason,
    and this test is verified by making it `async` and inserting a bare
    `await asyncio.sleep(0)` between the check and the increment: all fifty are
    admitted and the assertion below fails.
    """
    ceiling = Decimal("1.00")
    worst_case = ceiling  # one claim fills the ceiling exactly
    guard = _empty_window_guard(session_factory, daily_limit_usd=ceiling, refresh_seconds=1000.0)
    # Primed, so `reserve()` does not suspend on a refresh: the race under test
    # is the one *after* the totals are read, and a cold cache would also make
    # fifty concurrent requests open fifty connections.
    await guard.check()

    outcomes = await asyncio.gather(
        *(guard.reserve(worst_case) for _ in range(50)), return_exceptions=True
    )

    admitted = [o for o in outcomes if isinstance(o, Reservation)]
    refused = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(admitted) == 1, f"{len(admitted)} requests admitted against a one-request ceiling"
    assert len(refused) == 49
    assert all(isinstance(o, BudgetPressure) for o in refused)
    assert guard.reserved == worst_case


def test_the_critical_section_cannot_acquire_a_suspension_point() -> None:
    """The enforcement, asserted rather than trusted to review.

    A comment saying "do not await here" is advice; a synchronous function is a
    structure. Adding an await inside `_admit` requires making it `async` and
    changing its caller, which is a diff a reviewer sees — the same argument
    `snapshot()` makes for staying `def` so a scrape cannot learn to query.
    """
    import inspect

    assert not inspect.iscoroutinefunction(SpendGuard._admit)
    assert not inspect.iscoroutinefunction(SpendGuard.snapshot)
    assert not inspect.iscoroutinefunction(SpendGuard.record)


async def test_pressure_refusals_get_their_own_counter_not_the_trip_counter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A gauge scraped every 15s cannot see a three-second spike, so a demo
    refusing a third of its arrivals to reservation pressure reads as healthy on
    `budget_remaining`. The counter is the only series that can observe it — and
    it is emitted from zero, so `absent()` on it means "this replica is not
    reporting" rather than "nothing has happened yet"."""
    marker = f"budget-{uuid.uuid4()}"
    question = f"{marker} probe"
    ceiling = await _spend_so_far_today(session_factory) + Generator(FakeLLMClient()).max_cost(
        question, CHUNKS
    )
    client = SlowClient(delay=0.3)
    app = _budgeted_app(client, session_factory, daily_budget_usd=ceiling)
    metrics = app.state.rag.metrics

    # The series exists before anything has happened to it.
    assert "rag_qa_budget_pressure_total 0" in metrics.render()

    try:
        first = asyncio.create_task(post(app, "/query", {"question": question}))
        await asyncio.sleep(0.1)
        refused = await post(app, "/query", {"question": question})
        await first
    finally:
        await _cleanup(session_factory, marker)

    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "budget_pressure"
    assert metrics.budget_pressure_refusals == 1
    assert "rag_qa_budget_pressure_total 1" in metrics.render()
    # ...and it did not land in either of the counters that mean something else.
    assert metrics.budget_trips == {}
    assert metrics.requests_shed == 0


async def test_a_cold_cache_serves_a_burst_with_one_query_not_one_each(
    pooled_engine: AsyncEngine,
) -> None:
    """Staleness detection and the query are two steps with an await between
    them, so N requests arriving on an unrefreshed replica each read "stale"
    before any of them writes a result — N aggregates, N connections, against a
    refresh deliberately outside `RESERVED_CONNECTIONS` because it was assumed
    to be one checkout per TTL.

    **A cold cache is not an edge case, it is a deploy.** A rolling restart moves
    live traffic onto a process whose cache is empty by definition, so the
    assumption fails exactly when a replica is handed a burst.

    Both quantities are asserted, because they are different claims: one
    *statement* is the arithmetic KD-10 depends on, and one *checkout* is the
    resource it is protecting. Verified by removing the re-check inside the lock
    (twenty statements, serialized) and by removing the lock entirely.
    """
    statements: list[str] = []
    checkouts: list[object] = []

    def on_statement(*args: Any) -> None:
        if "query_log" in args[2]:
            statements.append(args[2])

    def on_checkout(*args: Any) -> None:
        checkouts.append(args[0])

    guard = SpendGuard(
        async_sessionmaker(pooled_engine, expire_on_commit=False),
        daily_limit_usd=Decimal("100000"),
        refresh_seconds=1000.0,
    )
    assert guard.refresh_count == 0, "the cache must be cold — that is the case under test"

    event.listen(pooled_engine.sync_engine, "before_cursor_execute", on_statement)
    event.listen(pooled_engine.sync_engine, "checkout", on_checkout)
    try:
        await asyncio.gather(*(guard.reserve(Decimal("0.001")) for _ in range(20)))
    finally:
        event.remove(pooled_engine.sync_engine, "before_cursor_execute", on_statement)
        event.remove(pooled_engine.sync_engine, "checkout", on_checkout)

    assert guard.refresh_count == 1, "twenty cold requests refreshed more than once"
    assert len(statements) == 1, f"{len(statements)} aggregates for one cold burst"
    assert len(checkouts) == 1, f"{len(checkouts)} connections for one cold burst"
    # ...and all twenty were served from the single result.
    assert guard.reserved == Decimal("0.020")


async def test_single_flight_did_not_move_the_suspension_point_into_admit() -> None:
    """The two properties are adjacent and pull in opposite directions: the
    refresh *must* await and share one result; the test-and-add must never
    await. Fixing the first by putting a lock around the second would serialize
    admissions behind a database query and reopen nothing — but it would make
    the structural guarantee depend on lock ordering rather than on a signature.
    """
    import inspect

    assert not inspect.iscoroutinefunction(SpendGuard._admit)
    assert inspect.iscoroutinefunction(SpendGuard._current)


# --- two ceilings, two kinds of traffic (KD-16 amendment 7) --------------------
#
# `recorded + outstanding` stopped being one sum. An eval's spend and its
# outstanding claim press the monthly cap -- that is the invoice -- and press the
# daily one not at all, because the daily ceiling shapes visitor burst and an
# eval must not close the demo for the rest of the day. Both directions are
# asserted, because both failures are silent and they fail opposite ways.


async def test_an_eval_reservation_cannot_trip_the_daily_ceiling(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Direction one. Without the fork, a 50-question run reserving its worst
    case would exhaust a $0.64 derived daily ceiling on its own and take the demo
    down for the rest of the day -- to measure how good the demo is."""
    guard = _empty_window_guard(
        session_factory,
        daily_limit_usd=Decimal("0.64"),
        monthly_limit_usd=Decimal("20.00"),
        refresh_seconds=1000.0,
    )
    await guard.check()

    # An eval claims far more than the whole day's visitor ceiling.
    evaluation = await guard.reserve(Decimal("3.90"), source=SpendSource.EVAL)
    assert guard.reserved == Decimal("3.90")
    assert guard.reserved_against_daily == Decimal("0")

    # A visitor arriving mid-run is served: the daily window never saw the claim.
    visitor = await guard.reserve(Decimal("0.01"))
    assert guard.reserved_against_daily == Decimal("0.01")

    visitor.settle(Decimal("0.008"))
    evaluation.settle(Decimal("0.65"))
    assert guard.reserved == Decimal("0")


async def test_an_eval_reservation_is_not_invisible_to_the_monthly_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Direction two, and the one that would cost real money. The monthly figure
    is the invoice; an eval run that the invoice cannot see spends it anyway."""
    guard = _empty_window_guard(
        session_factory,
        daily_limit_usd=Decimal("0.64"),
        monthly_limit_usd=Decimal("20.00"),
        refresh_seconds=1000.0,
    )
    await guard.check()

    await guard.reserve(Decimal("20.00"), source=SpendSource.EVAL)
    # The month is now committed, so a *visitor* is refused -- by the monthly
    # ceiling, not the daily one, and with the monthly reset.
    with pytest.raises(BudgetPressure) as pressure:
        await guard.reserve(Decimal("0.01"))
    assert pressure.value.ceiling == "monthly"


async def test_eval_spend_presses_the_month_and_not_the_day_once_recorded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The same fork on the *recorded* side. A settled eval is money gone, and it
    has to leave the daily headroom untouched and the monthly headroom lower."""
    guard = _empty_window_guard(
        session_factory,
        daily_limit_usd=Decimal("0.64"),
        monthly_limit_usd=Decimal("20.00"),
        refresh_seconds=1000.0,
    )
    await guard.check()
    before = guard.snapshot(datetime.now(UTC))
    assert before is not None

    guard.record(Decimal("0.65"), source=SpendSource.EVAL)

    after = guard.snapshot(datetime.now(UTC))
    assert after is not None
    assert after.remaining["daily"] == before.remaining["daily"], "an eval closed the demo"
    assert after.remaining["monthly"] == before.remaining["monthly"] - Decimal("0.65")


async def test_the_daily_window_reads_only_visitor_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The other half of the fork lives in SQL, and a test on the in-process
    deltas alone would never touch it: a row already in `query_log` and tagged
    `eval` must be in the month's total and out of the day's."""
    marker = f"budget-{uuid.uuid4()}"
    future = datetime.now(UTC) + timedelta(days=400)
    await _log_spend(session_factory, marker, "4.00", future, source=SpendSource.EVAL)
    await _log_spend(session_factory, marker, "0.10", future, source=SpendSource.VISITOR)
    try:
        guard = SpendGuard(
            session_factory,
            daily_limit_usd=Decimal("5.00"),
            monthly_limit_usd=Decimal("20.00"),
            now=lambda: future,
            refresh_seconds=1000.0,
        )
        await guard.check()
        snapshot = guard.snapshot(future)
        assert snapshot is not None
        # Read against the ceiling actually in force: a daily override alongside
        # a monthly budget is capped at 2x derived, so a hardcoded figure here
        # would be asserting the burst cap rather than the source filter.
        daily_ceiling = guard.daily_limit_for(future)
        assert daily_ceiling is not None
        # Day sees $0.10 (the visitor row); month sees $4.10 (both).
        assert snapshot.remaining["daily"] == daily_ceiling - Decimal("0.10")
        assert snapshot.remaining["monthly"] == Decimal("20.00") - Decimal("4.10")
    finally:
        await _cleanup(session_factory, marker)


async def test_two_overlapping_runs_against_headroom_that_admits_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A scheduled run and a manual one, started minutes apart.

    Check-then-spend is the design amendment 5 replaced: both would check, both
    would pass, and neither would see the other until it finished. The run-level
    reservation is the same mechanism at a coarser grain -- and the assertion is
    that the *second* run is refused while the first is still going, not that
    both eventually fit.
    """
    # A month exactly one run wide: the first is admitted (its own claim is never
    # counted against it), the second meets the first's outstanding claim.
    worst_case_run = Decimal("3.90")  # 50 questions at the reserved worst case
    guard = _empty_window_guard(
        session_factory, monthly_limit_usd=worst_case_run, refresh_seconds=1000.0
    )
    await guard.check()

    first = await guard.reserve(worst_case_run, source=SpendSource.EVAL)
    with pytest.raises((BudgetPressure, BudgetExhausted)):
        await guard.reserve(worst_case_run, source=SpendSource.EVAL)

    # ...and once the first settles to what it actually cost, the second fits.
    first.settle(Decimal("0.65"))
    second = await guard.reserve(worst_case_run, source=SpendSource.EVAL)
    second.settle(Decimal("0.65"))
    assert guard.reserved == Decimal("0")
    assert guard.recorded == Decimal("1.30")


async def test_a_killed_run_releases_its_reservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An eval run is exactly the thing a person ctrl-Cs after seeing the first
    few results. `KeyboardInterrupt` is not an `Exception`, so an `except
    Exception` cleanup would miss it and a run-length claim would outlive the
    run -- blocking the next one for the rest of the month."""
    guard = _empty_window_guard(
        session_factory, monthly_limit_usd=Decimal("20.00"), refresh_seconds=1000.0
    )
    reservation = await guard.reserve(Decimal("3.90"), source=SpendSource.EVAL)

    with pytest.raises(KeyboardInterrupt), reservation:
        raise KeyboardInterrupt

    assert guard.reserved == Decimal("0")


async def test_the_context_manager_releases_on_a_clean_exit_too(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    guard = _empty_window_guard(
        session_factory, monthly_limit_usd=Decimal("20.00"), refresh_seconds=1000.0
    )
    async with asyncio.timeout(5):
        reservation = await guard.reserve(Decimal("3.90"), source=SpendSource.EVAL)
        with reservation:
            reservation.settle(Decimal("0.65"))
    assert guard.reserved == Decimal("0")
    assert guard.recorded == Decimal("0.65")


# --- the guard has to be armed, not merely available (KD-16 amendment 8) ------
#
# Five review rounds went into making the ceiling exact, and it was unset by
# default: `RAG_QA_MONTHLY_BUDGET_USD` absent means no ceiling, no breaker, no
# reservations, and no headroom series to alert on. Everything else in this
# service fails closed. This was the one thing that failed open, in front of a
# metered API.


def test_serving_without_a_spend_ceiling_is_a_startup_error() -> None:
    with pytest.raises(ConfigurationError, match="no spend ceiling configured"):
        settings().require_serving(needs_providers=False)


def test_an_unlimited_deployment_must_say_so_out_loud() -> None:
    """The opt-out exists — a local run against a stubbed provider has nothing to
    protect — but it is a sentence someone typed, which is the whole difference
    between an unguarded deployment and an unnoticed one."""
    settings(allow_unlimited_spend=True).require_serving(needs_providers=False)


@pytest.mark.parametrize(
    "configured",
    [{"monthly_budget_usd": Decimal("20.00")}, {"daily_budget_usd": Decimal("0.50")}],
    ids=["monthly", "daily-only"],
)
def test_either_ceiling_alone_satisfies_the_requirement(configured: dict[str, Decimal]) -> None:
    settings(**configured).require_serving(needs_providers=False)


async def test_a_configured_ceiling_that_is_not_armed_fails_at_startup() -> None:
    """Configured is not the same as armed. `SpendGuard.enabled` needs a session
    factory as well as a limit, so a deployment that sets a budget and reaches
    lifespan without a database handle would serve with the ceiling silently
    off — the operator having done everything right and been overruled by a
    wiring gap."""
    app = build_app(daily_budget_usd=Decimal("5.00"))  # no session_factory
    assert not app.state.rag.budget.enabled
    with pytest.raises(ConfigurationError, match="not armed"):
        async with app.router.lifespan_context(app):
            pass


async def test_an_armed_ceiling_starts_normally(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The other side of the pair, so the check above is not passing because
    lifespan raises for some unrelated reason."""
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    assert app.state.rag.budget.enabled
    async with app.router.lifespan_context(app):
        pass
